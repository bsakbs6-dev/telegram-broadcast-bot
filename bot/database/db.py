import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from bot.config import config
from bot.database.models import (
    Base,
    SystemSetting,
    User,
    Whitelist,
    BroadcastSettings,
)

logger = logging.getLogger(__name__)

# Создание асинхронного движка SQLAlchemy
engine: AsyncEngine = create_async_engine(
    config.DATABASE_URL,
    echo=False,
    future=True,
)

# Фабрика асинхронных сессий
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Контекстный менеджер для безопасного получения асинхронной сессии БД.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error(f"Database session rollback due to error: {e}", exc_info=True)
            raise

async def init_db() -> None:
    """
    Инициализация базы данных: создание таблиц и установка значений по умолчанию.
    """
    logger.info("Initializing database tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Автомиграция колонки two_factor_password если таблица уже существовала
        try:
            from sqlalchemy import text
            await conn.execute(text("ALTER TABLE user_sessions ADD COLUMN two_factor_password VARCHAR(255)"))
        except Exception:
            pass

    # Установка настроек по умолчанию (режим доступа - закрытый/whitelist)
    async with get_session() as session:
        # Проверка настройки access_mode
        stmt = select(SystemSetting).where(SystemSetting.key == "access_mode")
        result = await session.execute(stmt)
        setting = result.scalar_one_or_none()

        if not setting:
            session.add(SystemSetting(key="access_mode", value="whitelist"))
            logger.info("Default access_mode set to 'whitelist'.")

        # Добавляем ADMIN_ID в Whitelist по умолчанию, чтобы админ всегда имел доступ
        admin_whitelist_stmt = select(Whitelist).where(Whitelist.telegram_id == config.ADMIN_ID)
        admin_wl = (await session.execute(admin_whitelist_stmt)).scalar_one_or_none()
        if not admin_wl:
            session.add(Whitelist(telegram_id=config.ADMIN_ID))
            logger.info(f"Admin ID {config.ADMIN_ID} added to Whitelist.")

    logger.info("Database initialized successfully.")

async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
) -> User:
    """
    Получение или создание пользователя с инициализацией BroadcastSettings.
    """
    stmt = select(User).where(User.id == telegram_id)
    user = (await session.execute(stmt)).scalar_one_or_none()

    is_admin = (telegram_id == config.ADMIN_ID)

    if not user:
        user = User(
            id=telegram_id,
            username=username,
            first_name=first_name,
            is_admin=is_admin,
        )
        session.add(user)
        await session.flush()

        # Создаем настройки рассылки по умолчанию
        broadcast_settings = BroadcastSettings(
            user_id=telegram_id,
            interval_minutes=config.DEFAULT_INTERVAL_MINUTES,
            random_jitter_min=config.DEFAULT_JITTER_MIN_SEC,
            random_jitter_max=config.DEFAULT_JITTER_MAX_SEC,
            rotation_mode="random",
            is_running=False,
        )
        session.add(broadcast_settings)
        await session.flush()
    else:
        # Обновляем актуальную информацию о пользователе
        user.username = username
        user.first_name = first_name
        user.is_admin = is_admin

        # Проверяем наличие broadcast_settings
        settings_stmt = select(BroadcastSettings).where(BroadcastSettings.user_id == telegram_id)
        settings = (await session.execute(settings_stmt)).scalar_one_or_none()
        if not settings:
            session.add(BroadcastSettings(
                user_id=telegram_id,
                interval_minutes=config.DEFAULT_INTERVAL_MINUTES,
                random_jitter_min=config.DEFAULT_JITTER_MIN_SEC,
                random_jitter_max=config.DEFAULT_JITTER_MAX_SEC,
                rotation_mode="random",
                is_running=False,
            ))
            await session.flush()

    return user

async def is_user_allowed(
    session: AsyncSession,
    telegram_id: int,
    username: Optional[str] = None,
) -> bool:
    """
    Проверка прав доступа пользователя:
    1. Администратор (ADMIN_ID) всегда имеет доступ.
    2. Если access_mode == 'public', доступ открыт для всех.
    3. Если access_mode == 'whitelist', проверяется наличие в таблице Whitelist (по id или @username).
    """
    if telegram_id == config.ADMIN_ID:
        return True

    # Проверяем системный режим доступа
    mode_stmt = select(SystemSetting.value).where(SystemSetting.key == "access_mode")
    mode_res = await session.execute(mode_stmt)
    access_mode = mode_res.scalar_one_or_none() or "whitelist"

    if access_mode == "public":
        return True

    # Проверяем Whitelist по telegram_id
    id_stmt = select(Whitelist).where(Whitelist.telegram_id == telegram_id)
    if (await session.execute(id_stmt)).scalar_one_or_none():
        return True

    # Проверяем Whitelist по username (без знака @)
    if username:
        clean_uname = username.lstrip("@").lower()
        uname_stmt = select(Whitelist).where(func.lower(Whitelist.username) == clean_uname)
        if (await session.execute(uname_stmt)).scalar_one_or_none():
            return True

    return False
