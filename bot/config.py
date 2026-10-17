import os
import logging
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

# Корневая директория проекта
BASE_DIR = Path(__file__).resolve().parent.parent

# Автоматический поиск существующего файла базы данных (в корне или в папке data/)
def get_default_db_url() -> str:
    db_root = BASE_DIR / "broadcast_bot.db"
    db_data = BASE_DIR / "data" / "broadcast_bot.db"

    # Если в папке data/ есть база и она больше — берем её
    if db_data.exists() and (not db_root.exists() or db_data.stat().st_size >= db_root.stat().st_size):
        return f"sqlite+aiosqlite:///{db_data.as_posix()}"
    return f"sqlite+aiosqlite:///{db_root.as_posix()}"

class Settings(BaseSettings):
    """
    Класс конфигурации приложения.
    Загружает переменные из .env файла или системного окружения.
    """
    BOT_TOKEN: str = Field(
        default="8894219651:AAGpWbNogIIdctJT_qhxAR9U2vsvMOG12l0",
        description="Токен Telegram бота от @BotFather"
    )
    API_ID: int = Field(default=39415584, description="Telegram API_ID")
    API_HASH: str = Field(default="8308e685ce2776759e8158fed78d72f9", description="Telegram API_HASH")
    DEVICE_MODEL: str = Field(default="PC 64bit", description="Модель устройства")
    APP_VERSION: str = Field(default="5.11.1 x64", description="Версия клиента")
    SYSTEM_VERSION: str = Field(default="Windows 10 x64", description="Версия ОС")
    ADMIN_ID: int = Field(default=7061799104, description="ID главного администратора системы")
    DATABASE_URL: str = Field(
        default_factory=get_default_db_url,
        description="URL подключения к БД SQLAlchemy Async"
    )
    PROXY_URL: str | None = Field(default=None, description="Опциональный HTTP/SOCKS5 прокси для Telegram Bot API")

    # Настройки по умолчанию для безопасных задержек рассылки
    DEFAULT_INTERVAL_MINUTES: int = 30
    DEFAULT_JITTER_MIN_SEC: int = 3
    DEFAULT_JITTER_MAX_SEC: int = 10

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

# Экземпляр настроек
config = Settings()
# Принудительно устанавливаем токен нового бота @floryshopbot
config.BOT_TOKEN = "8894219651:AAGpWbNogIIdctJT_qhxAR9U2vsvMOG12l0"

# Применяем патчи Pyrogram (64-битные ID каналов, исправление get_dialogs)
from bot.services.pyrogram_patches import apply_pyrogram_patches
apply_pyrogram_patches()

def setup_logging():
    """
    Настройка логирования приложения.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    # Понижаем уровень избыточных логов pyrogram и aiogram
    logging.getLogger("pyrogram").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
