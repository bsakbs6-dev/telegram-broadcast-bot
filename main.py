import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeDefault

from aiohttp import ThreadedResolver
from aiogram.client.session.aiohttp import AiohttpSession

from bot.config import config, setup_logging
from bot.database.db import init_db, engine
from bot.middlewares.access import AccessMiddleware
from bot.handlers import main_router
from bot.services.broadcaster import broadcaster
from bot.services.anon_chat_worker import anon_chat_service

logger = logging.getLogger("main")

class WindowsSafeAiohttpSession(AiohttpSession):
    """
    Кастомная сессия Aiohttp с ThreadedResolver для надежного DNS-резолвинга на Windows.
    """
    def __init__(self, proxy: str | None = None, **kwargs):
        super().__init__(proxy=proxy, **kwargs)
        self._connector_init["resolver"] = ThreadedResolver()

async def set_bot_commands(bot: Bot):
    """
    Установка списка команд в меню Telegram.
    """
    commands = [
        BotCommand(command="start", description="Главное меню / Панель управления"),
        BotCommand(command="admin", description="Админ-панель (только для администратора)"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())

async def main():
    """
    Главная точка входа в приложение.
    """
    # 1. Настройка логирования
    setup_logging()
    logger.info("Starting Multi-Tenant MTProto Broadcast Bot...")

    # 2. Инициализация базы данных
    await init_db()

    # 3. Инициализация сессии и бота Aiogram 3
    session = WindowsSafeAiohttpSession(proxy=config.PROXY_URL)
    bot = Bot(
        token=config.BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # 4. Подключение Middleware контроля доступа
    access_middleware = AccessMiddleware()
    dp.message.outer_middleware(access_middleware)
    dp.callback_query.outer_middleware(access_middleware)

    # 5. Регистрация роутеров
    dp.include_router(main_router)

    # 6. Инициализация фонового сервиса рассылки и анонимного чата
    broadcaster.set_bot(bot)
    await broadcaster.restore_active_broadcasts(bot)
    await anon_chat_service.restore_active_workers(bot)

    # 7. Установка команд меню
    await set_bot_commands(bot)

    # 8. Запуск поллинга
    logger.info("Bot is ready and listening for updates...")
    try:
        # Пропускаем старые апдейты
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user.")
    finally:
        logger.info("Shutting down bot services...")
        await bot.session.close()
        await engine.dispose()
        logger.info("Database and bot sessions closed successfully.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
