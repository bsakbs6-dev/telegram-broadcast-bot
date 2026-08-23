import os
import logging
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
import pyrogram.utils

# Поддержка 64-битных ID каналов Telegram в Pyrogram
pyrogram.utils.MIN_CHANNEL_ID = -1009999999999999

# Корневая директория проекта
BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    """
    Класс конфигурации приложения.
    Загружает переменные из .env файла или системного окружения.
    """
    BOT_TOKEN: str = Field(..., description="Токен Telegram бота от @BotFather")
    API_ID: int = Field(default=39415584, description="Telegram API_ID")
    API_HASH: str = Field(default="8308e685ce2776759e8158fed78d72f9", description="Telegram API_HASH")
    DEVICE_MODEL: str = Field(default="Desktop", description="Модель устройства")
    APP_VERSION: str = Field(default="1.0.0", description="Версия клиента")
    SYSTEM_VERSION: str = Field(default="Windows", description="Версия ОС")
    ADMIN_ID: int = Field(default=7061799104, description="ID главного администратора системы")
    DATABASE_URL: str = Field(
        default=f"sqlite+aiosqlite:///{BASE_DIR / 'broadcast_bot.db'}",
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
