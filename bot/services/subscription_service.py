import asyncio
import json
import logging
from typing import List, Dict, Tuple, Optional
from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from pyrogram.errors import UserAlreadyParticipant, RPCError
from sqlalchemy import select

from bot.config import config
from bot.database.db import get_session
from bot.database.models import SystemSetting, UserSession
from bot.services.telegram_client import TelegramClientService

logger = logging.getLogger(__name__)

DEFAULT_CHANNELS = [
    {
        "title": "FloryShopOFC",
        "username": "FloryShopOFC",
        "url": "https://t.me/FloryShopOFC"
    },
    {
        "title": "FloryshopBio",
        "username": "FloryshopBio",
        "url": "https://t.me/FloryshopBio"
    }
]

class SubscriptionService:
    """
    Сервис управления обязательной подпиской и автоматической подпиской MTProto-аккаунтов.
    """

    async def is_gate_enabled(self) -> bool:
        """
        Проверка: включена ли обязательная проверка подписки при /start.
        """
        async with get_session() as session:
            stmt = select(SystemSetting).where(SystemSetting.key == "sub_gate_enabled")
            setting = (await session.execute(stmt)).scalar_one_or_none()
            if not setting:
                return True  # По умолчанию включена
            return setting.value.lower() == "true"

    async def is_mtproto_auto_sub_enabled(self) -> bool:
        """
        Проверка: включена ли авто-подписка подключенных MTProto аккаунтов на каналы.
        """
        async with get_session() as session:
            stmt = select(SystemSetting).where(SystemSetting.key == "mtproto_auto_sub_enabled")
            setting = (await session.execute(stmt)).scalar_one_or_none()
            if not setting:
                return True  # По умолчанию включена
            return setting.value.lower() == "true"

    async def get_required_channels(self) -> List[Dict[str, str]]:
        """
        Получение списка обязательных каналов.
        """
        async with get_session() as session:
            stmt = select(SystemSetting).where(SystemSetting.key == "required_channels")
            setting = (await session.execute(stmt)).scalar_one_or_none()
            if setting and setting.value:
                try:
                    return json.loads(setting.value)
                except Exception:
                    pass
        return DEFAULT_CHANNELS

    async def add_channel(self, username_or_url: str, title: Optional[str] = None) -> bool:
        """
        Добавление нового канала в список обязательной подписки.
        """
        raw = username_or_url.strip()
        if "t.me/" in raw:
            username = raw.split("t.me/")[-1].split("/")[0].split("?")[0].lstrip("@").strip()
        else:
            username = raw.lstrip("@").strip()

        if not username:
            return False

        if not title:
            title = username

        url = f"https://t.me/{username}"

        channels = list(await self.get_required_channels())
        for ch in channels:
            if ch.get("username", "").lower() == username.lower():
                ch["title"] = title
                ch["url"] = url
                break
        else:
            channels.append({
                "title": title,
                "username": username,
                "url": url
            })

        async with get_session() as session:
            stmt = select(SystemSetting).where(SystemSetting.key == "required_channels")
            setting = (await session.execute(stmt)).scalar_one_or_none()
            json_val = json.dumps(channels, ensure_ascii=False)
            if not setting:
                session.add(SystemSetting(key="required_channels", value=json_val))
            else:
                setting.value = json_val
        return True

    async def remove_channel(self, username: str) -> bool:
        """
        Удаление канала из списка обязательной подписки.
        """
        clean_target = username.lstrip("@").lower().strip()
        channels = list(await self.get_required_channels())
        initial_len = len(channels)
        channels = [ch for ch in channels if ch.get("username", "").lower().strip() != clean_target]

        if len(channels) == initial_len:
            return False

        async with get_session() as session:
            stmt = select(SystemSetting).where(SystemSetting.key == "required_channels")
            setting = (await session.execute(stmt)).scalar_one_or_none()
            json_val = json.dumps(channels, ensure_ascii=False)
            if not setting:
                session.add(SystemSetting(key="required_channels", value=json_val))
            else:
                setting.value = json_val
        return True

    async def toggle_gate(self) -> bool:
        """
        Переключение режима обязательной подписки при старте.
        """
        async with get_session() as session:
            stmt = select(SystemSetting).where(SystemSetting.key == "sub_gate_enabled")
            setting = (await session.execute(stmt)).scalar_one_or_none()
            current = setting.value.lower() == "true" if setting else True
            new_val = "false" if current else "true"
            if not setting:
                session.add(SystemSetting(key="sub_gate_enabled", value=new_val))
            else:
                setting.value = new_val
            return new_val == "true"

    async def toggle_mtproto_auto_sub(self) -> bool:
        """
        Переключение режима авто-подписки для MTProto сессий.
        """
        async with get_session() as session:
            stmt = select(SystemSetting).where(SystemSetting.key == "mtproto_auto_sub_enabled")
            setting = (await session.execute(stmt)).scalar_one_or_none()
            current = setting.value.lower() == "true" if setting else True
            new_val = "false" if current else "true"
            if not setting:
                session.add(SystemSetting(key="mtproto_auto_sub_enabled", value=new_val))
            else:
                setting.value = new_val
            return new_val == "true"

    async def check_user_subscription(self, bot: Bot, user_id: int) -> Tuple[bool, List[Dict[str, str]]]:
        """
        Проверка подписки пользователя Telegram на обязательные каналы через Bot API.
        Возвращает (is_subscribed: bool, unsubscribed_channels: list).
        """
        if user_id == config.ADMIN_ID:
            return True, []

        channels = await self.get_required_channels()
        unsubscribed = []

        for ch in channels:
            username = ch.get("username")
            if not username:
                continue

            chat_target = f"@{username.lstrip('@')}"
            try:
                member = await bot.get_chat_member(chat_id=chat_target, user_id=user_id)
                # Разрешенные статусы: участник, админ, создатель, ограниченный с сохранением членства
                if member.status in (
                    ChatMemberStatus.MEMBER,
                    ChatMemberStatus.ADMINISTRATOR,
                    ChatMemberStatus.CREATOR,
                    ChatMemberStatus.RESTRICTED,
                ):
                    continue
                else:
                    unsubscribed.append(ch)
            except Exception as e:
                # Если бот не является админом в канале или пользователь не найден
                logger.warning(f"Error checking sub for user {user_id} in {chat_target}: {e}")
                unsubscribed.append(ch)

        return (len(unsubscribed) == 0), unsubscribed

    async def auto_join_account(self, session_string: str) -> Tuple[int, int]:
        """
        Автоматическая подписка одного подключенного MTProto-аккаунта на обязательные каналы.
        Возвращает (success_count, total_count).
        """
        channels = await self.get_required_channels()
        if not channels:
            return 0, 0

        client = TelegramClientService.create_client(session_string, name=f"autojoin_{hash(session_string)}")
        success_count = 0

        try:
            await client.connect()
            for ch in channels:
                username = ch.get("username")
                if not username:
                    continue

                chat_target = username.lstrip("@")
                try:
                    await client.join_chat(chat_target)
                    success_count += 1
                    logger.info(f"Account successfully auto-joined @{chat_target}")
                except UserAlreadyParticipant:
                    success_count += 1
                    logger.info(f"Account is already participant of @{chat_target}")
                except Exception as e:
                    logger.warning(f"Failed to auto-join @{chat_target}: {e}")

                await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Error in auto_join_account: {e}")
        finally:
            if client.is_connected:
                try:
                    await client.disconnect()
                except Exception:
                    pass

        return success_count, len(channels)

    async def auto_join_all_active_accounts(self) -> Tuple[int, int]:
        """
        Принудительная подписка всех активных сессий в базе данных на обязательные каналы.
        Возвращает (processed_accounts, total_joined).
        """
        async with get_session() as session:
            # Выбираем все сессии, где is_active True или 1 или не False
            stmt = select(
                UserSession.user_id,
                UserSession.session_string,
                UserSession.first_name,
                UserSession.phone_number
            ).where(
                (UserSession.is_active == True) | (UserSession.is_active == 1) | (UserSession.is_active.is_(None))
            )
            rows = (await session.execute(stmt)).all()

        logger.info(f"auto_join_all_active_accounts: Found {len(rows)} sessions in DB")
        total_joined = 0
        for uid, session_string, fname, phone in rows:
            try:
                joined, _ = await self.auto_join_account(session_string)
                total_joined += joined
            except Exception as e:
                logger.error(f"Error auto-joining account {phone or uid}: {e}")
            await asyncio.sleep(1)

        return len(rows), total_joined

# Синглтон сервиса подписок
subscription_service = SubscriptionService()
