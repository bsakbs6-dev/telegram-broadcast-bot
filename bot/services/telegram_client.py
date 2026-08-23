import asyncio
import logging
from typing import List, Optional, Tuple, Dict, Any
from pyrogram import Client, utils
from pyrogram.enums import ChatType, ChatMemberStatus

# Патч для поддержки 64-битных ID каналов Telegram в Pyrogram
utils.MIN_CHANNEL_ID = -1009999999999999
from pyrogram.errors import (
    FloodWait,
    ChatWriteForbidden,
    UserBannedInChannel,
    ChannelPrivate,
    PeerFlood,
    UserDeactivated,
    AuthKeyUnregistered,
    SessionRevoked,
    ChannelInvalid,
    PeerIdInvalid,
    ChatAdminRequired,
    RPCError,
)
from sqlalchemy import select, delete

from bot.config import config
from bot.database.db import get_session
from bot.database.models import TargetChannel

logger = logging.getLogger(__name__)

class TelegramClientService:
    """
    Сервис для работы с пользовательскими MTProto сессиями:
    сканирование каналов, проверка валидности сессии, отправка сообщений.
    """

    @staticmethod
    def create_client(session_string: str, name: Optional[str] = None) -> Client:
        """
        Создает экземпляр in-memory Pyrogram Client из StringSession.
        """
        client_name = name or f"user_session_{hash(session_string)}"
        return Client(
            name=client_name,
            session_string=session_string,
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            device_model=config.DEVICE_MODEL,
            app_version=config.APP_VERSION,
            system_version=config.SYSTEM_VERSION,
            in_memory=True,
        )

    @classmethod
    async def validate_session(cls, session_string: str) -> Tuple[bool, Optional[Any], Optional[str]]:
        """
        Проверяет работоспособность сессии.
        Возвращает (is_valid: bool, me_obj: Optional[User], error_message: Optional[str]).
        """
        client = cls.create_client(session_string)
        try:
            await client.connect()
            me = await client.get_me()
            await client.disconnect()
            return True, me, None
        except (AuthKeyUnregistered, UserDeactivated, SessionRevoked):
            return False, None, "Сессия отозвана или аккаунт деактивирован."
        except FloodWait as e:
            return False, None, f"Временный FloodWait: {e.value} сек."
        except Exception as e:
            logger.error(f"Error validating session: {e}", exc_info=True)
            return False, None, f"Ошибка подключения: {str(e)}"
        finally:
            if client.is_connected:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    @classmethod
    async def scan_and_save_dialogs(cls, user_id: int, session_string: str) -> Tuple[int, str]:
        """
        Сканирует MTProto диалоги пользователя, находит каналы и группы с правами публикации,
        сохраняет или обновляет их в базе данных.
        Возвращает (count_found: int, status_message: str).
        """
        client = cls.create_client(session_string, name=f"scan_{user_id}")
        try:
            await client.connect()
            me = await client.get_me()
            found_chats: List[Dict[str, Any]] = []
            seen_chat_ids = set()

            async for dialog in client.get_dialogs():
                chat = dialog.chat
                if not chat or chat.id in seen_chat_ids:
                    continue

                members_count = getattr(chat, "members_count", None)
                can_post = False
                chat_type_str = "group"

                # 1. Каналы-трансляторы (Channel)
                if chat.type == ChatType.CHANNEL:
                    chat_type_str = "channel"
                    try:
                        # Проверяем статус создателя/администратора в канале
                        member = await client.get_chat_member(chat.id, me.id)
                        if member.status in [ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR]:
                            if member.status == ChatMemberStatus.OWNER or (member.privileges and member.privileges.can_post_messages):
                                can_post = True
                    except Exception:
                        pass

                # 2. Супергруппы и обычные группы (Supergroup / Group)
                elif chat.type in [ChatType.SUPERGROUP, ChatType.GROUP]:
                    chat_type_str = "supergroup" if chat.type == ChatType.SUPERGROUP else "group"
                    
                    # Проверяем количество участников (от 5000)
                    if members_count is None:
                        try:
                            full_chat = await client.get_chat(chat.id)
                            members_count = getattr(full_chat, "members_count", 0)
                        except Exception:
                            members_count = 0

                    # Фильтр: только группы от 5000 участников
                    if members_count and members_count >= 5000:
                        # Проверяем, не заблокирована ли отправка сообщений для участников
                        if chat.permissions is None or chat.permissions.can_send_messages:
                            can_post = True

                if can_post:
                    seen_chat_ids.add(chat.id)
                    title = chat.title or (f"{chat.first_name or ''} {chat.last_name or ''}").strip() or f"Chat {chat.id}"
                    found_chats.append({
                        "chat_id": chat.id,
                        "title": title,
                        "username": chat.username,
                        "chat_type": chat_type_str,
                        "members_count": members_count,
                    })

            await client.disconnect()

            # Сохраняем найденные каналы в БД
            async with get_session() as session:
                # Получаем существующие каналы пользователя для сохранения их состояния выбора (is_selected)
                existing_stmt = select(TargetChannel).where(TargetChannel.user_id == user_id)
                existing_channels = (await session.execute(existing_stmt)).scalars().all()
                existing_map = {c.chat_id: c.is_selected for c in existing_channels}

                # Удаляем старые записи, чтобы синхронизировать список
                await session.execute(delete(TargetChannel).where(TargetChannel.user_id == user_id))

                for fc in found_chats:
                    # Если канал уже был, сохраняем выбор пользователя, иначе True
                    is_selected = existing_map.get(fc["chat_id"], True)
                    session.add(TargetChannel(
                        user_id=user_id,
                        chat_id=fc["chat_id"],
                        title=fc["title"],
                        username=fc["username"],
                        chat_type=fc["chat_type"],
                        is_selected=is_selected,
                        can_send_messages=True,
                        last_status="ready"
                    ))

            logger.info(f"Scanned {len(found_chats)} available chats/channels for user {user_id}")
            return len(found_chats), f"Найдено доступных каналов и групп: {len(found_chats)}"

        except (AuthKeyUnregistered, UserDeactivated, SessionRevoked):
            return 0, "❌ Сессия аккаунта устарела или отозвана. Переподключите аккаунт."
        except FloodWait as e:
            return 0, f"⚠️ FloodWait от Telegram: подождите {e.value} секунд."
        except Exception as e:
            logger.error(f"Error scanning dialogs for user {user_id}: {e}", exc_info=True)
            return 0, f"❌ Ошибка сканирования: {str(e)}"
        finally:
            if client.is_connected:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    @classmethod
    async def send_broadcast_message(
        cls,
        client: Client,
        chat_id: int,
        text: str,
    ) -> Tuple[bool, str]:
        """
        Отправляет сообщение в указанный чат через подключенный клиент Pyrogram.
        Возвращает (success: bool, status_message: str).
        """
        try:
            await client.send_message(
                chat_id=chat_id,
                text=text,
                disable_web_page_preview=False,
            )
            return True, "ok"

        except FloodWait as e:
            logger.warning(f"FloodWait in send_message (chat {chat_id}): sleeping {e.value}s")
            await asyncio.sleep(e.value + 1)
            # Пробуем отправить повторно после ожидания
            try:
                await client.send_message(
                    chat_id=chat_id,
                    text=text,
                    disable_web_page_preview=False,
                )
                return True, "ok"
            except Exception as retry_err:
                return False, f"flood_wait_retry_failed: {retry_err}"

        except (ChatWriteForbidden, ChatAdminRequired):
            logger.info(f"ChatWriteForbidden in chat {chat_id}")
            return False, "forbidden"

        except UserBannedInChannel:
            logger.info(f"UserBannedInChannel in chat {chat_id}")
            return False, "banned"

        except ChannelPrivate:
            logger.info(f"ChannelPrivate in chat {chat_id}")
            return False, "channel_private"

        except PeerFlood:
            logger.warning(f"PeerFlood reported by Telegram in chat {chat_id}")
            return False, "peer_flood"

        except (ChannelInvalid, PeerIdInvalid):
            logger.info(f"Invalid peer ID {chat_id}")
            return False, "peer_invalid"

        except (AuthKeyUnregistered, UserDeactivated, SessionRevoked) as e:
            logger.error(f"Critical session error during broadcast: {e}")
            return False, "session_revoked"

        except RPCError as e:
            logger.warning(f"Telegram RPCError in chat {chat_id}: {e}")
            return False, f"rpc_error: {e.MESSAGE or str(e)}"

        except Exception as e:
            logger.error(f"Unexpected error sending message to {chat_id}: {e}", exc_info=True)
            return False, f"error: {str(e)}"

    @classmethod
    async def get_latest_login_code(
        cls,
        session_string: str,
    ) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        """
        Считывает последнее служебное сообщение из чата 777000 (Telegram Notifications)
        и извлекает код подтверждения для входа на аккаунт.
        Возвращает (success: bool, code: Optional[str], full_text: Optional[str], date_str: Optional[str]).
        """
        import re
        client = cls.create_client(session_string, name=f"read_code_{hash(session_string)}")
        try:
            await client.connect()
            async for message in client.get_chat_history(777000, limit=5):
                text = message.text or ""
                # Ищем 5-значный код авторизации
                match = re.search(r'(?:код|code|логин|login)?[\s:]*([0-9]{5})\b', text, re.IGNORECASE)
                extracted_code = match.group(1) if match else None
                if not extracted_code:
                    # Попробуем найти любые 5 цифр
                    digits_match = re.search(r'\b([0-9]{5})\b', text)
                    if digits_match:
                        extracted_code = digits_match.group(1)

                date_str = message.date.strftime("%Y-%m-%d %H:%M:%S") if message.date else "Неизвестно"
                await client.disconnect()
                return True, extracted_code or "Не найден в тексте", text, date_str

            await client.disconnect()
            return False, None, "В чате служебных уведомлений Telegram пока нет сообщений.", None

        except (AuthKeyUnregistered, UserDeactivated, SessionRevoked):
            return False, None, "Сессия аккаунта недействительна или отозвана.", None
        except Exception as e:
            logger.error(f"Error fetching service code from 777000: {e}", exc_info=True)
            return False, None, f"Ошибка чтения уведомлений: {str(e)}", None
        finally:
            if client.is_connected:
                try:
                    await client.disconnect()
                except Exception:
                    pass
