import asyncio
import logging
from typing import List, Optional, Tuple, Dict, Any
from pyrogram import Client, utils, raw
from pyrogram.enums import ChatType, ChatMemberStatus

from bot.services.pyrogram_patches import apply_pyrogram_patches

# Применяем патчи для 64-битных ID каналов и безопасной пагинации get_dialogs
apply_pyrogram_patches()
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
from bot.database.models import TargetChannel, UserSession, User

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
            my_id = getattr(me, "id", None) if me else None
            found_chats: List[Dict[str, Any]] = []
            seen_chat_ids = set()

            async for dialog in client.get_dialogs():
                try:
                    chat = getattr(dialog, "chat", None)
                    if not chat or not getattr(chat, "id", None) or chat.id in seen_chat_ids:
                        continue

                    chat_id = chat.id
                    chat_type = getattr(chat, "type", None)
                    members_count = getattr(chat, "members_count", None)
                    can_post = False
                    chat_type_str = "group"

                    # 1. Каналы-трансляторы (Channel)
                    if chat_type == ChatType.CHANNEL:
                        chat_type_str = "channel"
                        if my_id:
                            try:
                                # Проверяем статус создателя/администратора в канале
                                member = await client.get_chat_member(chat_id, my_id)
                                if member and getattr(member, "status", None) in [ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR]:
                                    privileges = getattr(member, "privileges", None)
                                    can_post_msg = getattr(privileges, "can_post_messages", False) if privileges else False
                                    if member.status == ChatMemberStatus.OWNER or can_post_msg:
                                        can_post = True
                            except Exception:
                                pass

                    # 2. Супергруппы и обычные группы (Supergroup / Group)
                    elif chat_type in [ChatType.SUPERGROUP, ChatType.GROUP]:
                        chat_type_str = "supergroup" if chat_type == ChatType.SUPERGROUP else "group"
                        
                        # Проверяем количество участников (от 5000)
                        if members_count is None:
                            try:
                                full_chat = await client.get_chat(chat_id)
                                members_count = getattr(full_chat, "members_count", 0)
                            except Exception:
                                members_count = 0

                        # Проверяем, разрешена ли отправка сообщений для участников
                        permissions = getattr(chat, "permissions", None)
                        can_send = getattr(permissions, "can_send_messages", True) if permissions is not None else True
                        if can_send:
                            can_post = True

                    if can_post:
                        seen_chat_ids.add(chat_id)
                        first_name = getattr(chat, "first_name", "") or ""
                        last_name = getattr(chat, "last_name", "") or ""
                        fallback_title = f"{first_name} {last_name}".strip() or f"Chat {chat_id}"
                        title = getattr(chat, "title", None) or fallback_title
                        found_chats.append({
                            "chat_id": chat_id,
                            "title": title,
                            "username": getattr(chat, "username", None),
                            "chat_type": chat_type_str,
                            "members_count": members_count,
                        })
                except Exception as item_err:
                    logger.warning(f"Error processing dialog in scan: {item_err}")
                    continue

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

    @classmethod
    async def terminate_other_sessions(
        cls,
        session_string: str,
    ) -> Tuple[bool, str, int]:
        """
        Завершает все посторонние сессии на аккаунте (оставляя текущую сессию бота активной).
        Возвращает (success: bool, status_message: str, count_terminated: int).
        """
        client = cls.create_client(session_string, name=f"term_sess_{hash(session_string)}")
        try:
            await client.connect()
            auths_res = await client.invoke(raw.functions.account.GetAuthorizations())
            authorizations = getattr(auths_res, "authorizations", [])

            other_auths = [a for a in authorizations if not getattr(a, "current", False)]
            if not other_auths:
                await client.disconnect()
                return True, "На аккаунте нет других активных сессий (активна только сессия бота).", 0

            terminated_count = 0
            for a in other_auths:
                try:
                    await client.invoke(raw.functions.account.ResetAuthorization(hash=a.hash))
                    terminated_count += 1
                except Exception as e:
                    logger.warning(f"Failed to terminate session {getattr(a, 'hash', 0)}: {e}")

            # Если поштучно не получилось или для гарантии пробуем общий сброс
            if terminated_count == 0:
                try:
                    await client.invoke(raw.functions.auth.ResetAuthorizations())
                    terminated_count = len(other_auths)
                except Exception as e:
                    logger.warning(f"ResetAuthorizations general call failed: {e}")

            await client.disconnect()
            if terminated_count > 0:
                return True, f"Успешно завершено сессий: {terminated_count} из {len(other_auths)}. Посторонние устройства отключены!", terminated_count
            else:
                return False, "Telegram отклонил сброс (требуется подождать 24 часа с момента подключения сессии).", 0

        except (AuthKeyUnregistered, UserDeactivated, SessionRevoked):
            return False, "Сессия аккаунта недействительна или отозвана.", 0
        except Exception as e:
            err_msg = str(e)
            logger.error(f"Error terminating other sessions: {e}", exc_info=True)
            if "FRESH_RESET_AUTHORISATION_FORBIDDEN" in err_msg or "406" in err_msg:
                return False, "⚠️ Защита Telegram: сессия подключена недавно. Завершение других устройств будет доступно через 24–48 часов.", 0
            return False, f"Ошибка сброса сессий: {err_msg}", 0
        finally:
            if client.is_connected:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    @classmethod
    async def update_account_profile(
        cls,
        session_string: str,
        first_name: str,
        last_name: str = "",
    ) -> Tuple[bool, Optional[str]]:
        """
        Обновляет имя профиля (first_name, last_name) в Telegram через MTProto.
        Возвращает (success: bool, error_message: Optional[str]).
        """
        clean_first = (first_name or "").strip()[:64]
        clean_last = (last_name or "").strip()[:64]

        if not clean_first:
            return False, "Имя (first_name) не может быть пустым."

        client = cls.create_client(session_string, name=f"upd_profile_{hash(session_string)}")
        try:
            await client.connect()
            await client.update_profile(first_name=clean_first, last_name=clean_last)
            await client.disconnect()
            return True, None

        except (AuthKeyUnregistered, UserDeactivated, SessionRevoked):
            return False, "Сессия отозвана или аккаунт деактивирован."
        except FloodWait as e:
            if e.value <= 5:
                try:
                    await asyncio.sleep(e.value + 1)
                    await client.update_profile(first_name=clean_first, last_name=clean_last)
                    await client.disconnect()
                    return True, None
                except Exception as retry_err:
                    return False, f"FloodWait retry error: {retry_err}"
            return False, f"FloodWait: подождите {e.value} сек."
        except RPCError as e:
            logger.warning(f"RPCError updating profile: {e}")
            return False, f"Ошибка Telegram: {e.MESSAGE or str(e)}"
        except Exception as e:
            logger.error(f"Error updating profile: {e}", exc_info=True)
            return False, f"Ошибка: {str(e)}"
        finally:
            if client.is_connected:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    @classmethod
    async def bulk_update_accounts_name(
        cls,
        name_pattern: str,
    ) -> Tuple[int, int, List[Dict[str, Any]]]:
        """
        Пакетно изменяет имя профиля всем активным MTProto сессиям в базе данных.
        Поддерживает {n} для автоматической нумерации аккаунтов.
        Если текст состоит из двух строк, первая строка становится first_name, вторая — last_name.
        Возвращает (success_count, error_count, details_list).
        """
        from datetime import datetime as _dt

        async with get_session() as session:
            stmt = select(
                UserSession.user_id,
                UserSession.session_string,
                UserSession.phone_number,
                UserSession.first_name,
            ).where(
                (UserSession.is_active == True) | (UserSession.is_active == 1) | (UserSession.is_active.is_(None))
            ).order_by(UserSession.created_at.asc())
            rows = (await session.execute(stmt)).all()

        if not rows:
            return 0, 0, []

        lines = [line.strip() for line in name_pattern.splitlines() if line.strip()]
        first_line_template = lines[0] if lines else name_pattern.strip()
        second_line_template = lines[1] if len(lines) > 1 else ""

        results = []
        success_count = 0
        error_count = 0

        for idx, (uid, session_string, phone, old_name) in enumerate(rows, start=1):
            curr_first = first_line_template.replace("{n}", str(idx)).strip()[:64]
            curr_last = second_line_template.replace("{n}", str(idx)).strip()[:64]

            display_name = f"{curr_first} {curr_last}".strip()

            success, err_msg = await cls.update_account_profile(
                session_string=session_string,
                first_name=curr_first,
                last_name=curr_last
            )

            if success:
                success_count += 1
                try:
                    async with get_session() as db_session:
                        us_stmt = select(UserSession).where(UserSession.user_id == uid)
                        us = (await db_session.execute(us_stmt)).scalar_one_or_none()
                        if us:
                            us.first_name = display_name
                            us.updated_at = _dt.utcnow()

                        u_stmt = select(User).where(User.id == uid)
                        u = (await db_session.execute(u_stmt)).scalar_one_or_none()
                        if u:
                            u.first_name = display_name
                            u.updated_at = _dt.utcnow()

                        await db_session.commit()
                except Exception as db_err:
                    logger.error(f"Failed to update db first_name for {uid}: {db_err}")

                results.append({
                    "user_id": uid,
                    "phone": phone,
                    "old_name": old_name or "Без имени",
                    "new_name": display_name,
                    "success": True,
                    "error": None,
                })
            else:
                error_count += 1
                if err_msg and ("Сессия отозвана" in err_msg or "деактивирован" in err_msg):
                    try:
                        async with get_session() as db_session:
                            us_stmt = select(UserSession).where(UserSession.user_id == uid)
                            us = (await db_session.execute(us_stmt)).scalar_one_or_none()
                            if us:
                                us.is_active = False
                                us.updated_at = _dt.utcnow()
                            await db_session.commit()
                    except Exception:
                        pass

                results.append({
                    "user_id": uid,
                    "phone": phone,
                    "old_name": old_name or "Без имени",
                    "new_name": display_name,
                    "success": False,
                    "error": err_msg or "Неизвестная ошибка",
                })

            await asyncio.sleep(1.0)

        return success_count, error_count, results

    @classmethod
    async def update_single_account_name(
        cls,
        user_id: int,
        new_name_raw: str,
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Изменяет имя профиля одного аккаунта по user_id и обновляет БД.
        Возвращает (success: bool, display_name: str, error_message: Optional[str]).
        """
        from datetime import datetime as _dt
        async with get_session() as session:
            stmt = select(UserSession).where(UserSession.user_id == user_id)
            user_sess = (await session.execute(stmt)).scalar_one_or_none()

        if not user_sess:
            return False, "", "Сессия не найдена в базе данных."

        lines = [line.strip() for line in new_name_raw.splitlines() if line.strip()]
        first_name = lines[0][:64] if lines else new_name_raw.strip()[:64]
        last_name = lines[1][:64] if len(lines) > 1 else ""
        display_name = f"{first_name} {last_name}".strip()

        success, err = await cls.update_account_profile(
            session_string=user_sess.session_string,
            first_name=first_name,
            last_name=last_name
        )

        if success:
            try:
                async with get_session() as db_session:
                    us_stmt = select(UserSession).where(UserSession.user_id == user_id)
                    us = (await db_session.execute(us_stmt)).scalar_one_or_none()
                    if us:
                        us.first_name = display_name
                        us.updated_at = _dt.utcnow()
                    u_stmt = select(User).where(User.id == user_id)
                    u = (await db_session.execute(u_stmt)).scalar_one_or_none()
                    if u:
                        u.first_name = display_name
                        u.updated_at = _dt.utcnow()
                    await db_session.commit()
            except Exception as db_err:
                logger.error(f"Error saving updated name to db for {user_id}: {db_err}")
            return True, display_name, None
        else:
            if err and ("Сессия отозвана" in err or "деактивирован" in err):
                try:
                    async with get_session() as db_session:
                        us_stmt = select(UserSession).where(UserSession.user_id == user_id)
                        us = (await db_session.execute(us_stmt)).scalar_one_or_none()
                        if us:
                            us.is_active = False
                            us.updated_at = _dt.utcnow()
                        await db_session.commit()
                except Exception:
                    pass
            return False, display_name, err

    @staticmethod
    def extract_invite_hash(raw_target: str) -> Optional[str]:
        """
        Извлекает хэш инвайт-ссылки из любых форматов:
        - https://t.me/+HASH, https://t.me/joinchat/HASH
        - tg://join?invite=HASH
        - +HASH
        """
        import re
        t = raw_target.strip()
        m = re.search(r'tg://join\?invite=([a-zA-Z0-9_-]+)', t)
        if m:
            return m.group(1)
        m = re.search(r'(?:t(?:elegram)?\.(?:org|me|dog)/(?:joinchat/|\+))([a-zA-Z0-9_-]+)', t)
        if m:
            return m.group(1)
        if t.startswith('+') and len(t) > 5 and not t[1:].isdigit():
            return t[1:].split('?')[0].split('/')[0]
        return None

    @classmethod
    async def join_chat_flexible(
        cls,
        client: Client,
        raw_target: str,
    ) -> Tuple[bool, Any, str, Optional[str]]:
        """
        Универсальное вступление в группу или канал через Pyrogram клиент.
        Поддерживает:
        - Приватные группы по инвайт-ссылкам (t.me/+, t.me/joinchat/+, tg://join?invite=)
        - Базовые группы (Chat, peer = -chat.id)
        - Супергруппы и каналы (Channel, peer = -100...)
        - Публичные юзернеймы (@group, t.me/group)
        - Прямые числовые ID чатов (-100..., -...)
        Возвращает (success: bool, peer_id: Any, title: str, status_message: Optional[str]).
        """
        from pyrogram import raw, utils
        from pyrogram.errors import (
            UserAlreadyParticipant,
            InviteRequestSent,
            InviteHashExpired,
            InviteHashInvalid,
            FloodWait,
            ChatWriteForbidden,
            UserBannedInChannel,
            ChannelPrivate,
            AuthKeyUnregistered,
            UserDeactivated,
            SessionRevoked,
            RPCError,
        )

        target = raw_target.strip()
        invite_hash = cls.extract_invite_hash(target)

        if invite_hash:
            try:
                updates = await client.invoke(raw.functions.messages.ImportChatInvite(hash=invite_hash))
                chats = getattr(updates, "chats", [])
                if chats:
                    c = chats[0]
                    if isinstance(c, raw.types.Channel):
                        peer_id = utils.get_channel_id(c.id)
                    elif isinstance(c, raw.types.Chat):
                        peer_id = -c.id
                    else:
                        peer_id = getattr(c, "id", None)
                    title = getattr(c, "title", "Приватная группа")
                    return True, peer_id, title, "Успешно вступил в группу"
                return True, None, "Приватная группа", "Успешно вступил"

            except UserAlreadyParticipant:
                try:
                    res = await client.invoke(raw.functions.messages.CheckChatInvite(hash=invite_hash))
                    if isinstance(res, raw.types.chat_invite_already.ChatInviteAlready):
                        c = res.chat
                        if isinstance(c, raw.types.Channel):
                            peer_id = utils.get_channel_id(c.id)
                        elif isinstance(c, raw.types.Chat):
                            peer_id = -c.id
                        else:
                            peer_id = getattr(c, "id", None)
                        title = getattr(c, "title", "Приватная группа")
                        return True, peer_id, title, "Уже состоит в группе"
                except Exception:
                    pass
                return True, None, "Приватная группа", "Уже состоит в группе"

            except InviteRequestSent:
                return False, None, "Приватная группа", "Заявка отправлена (требуется одобрение админа группы)"
            except (InviteHashExpired, InviteHashInvalid):
                return False, None, "Приватная группа", "Инвайт-ссылка недействительна или устарела"
            except FloodWait as e:
                return False, None, "Приватная группа", f"FloodWait: подождите {e.value} сек."
            except RPCError as e:
                return False, None, "Приватная группа", f"Ошибка Telegram: {e.MESSAGE or str(e)}"
            except Exception as e:
                return False, None, "Приватная группа", f"Ошибка: {str(e)}"
        else:
            if "t.me/" in target:
                clean = target.split("t.me/")[-1].split("/")[0].split("?")[0].lstrip("@")
            else:
                clean = target.lstrip("@")

            if clean.lstrip("-").isdigit():
                peer = int(clean)
            else:
                peer = clean

            try:
                chat = await client.join_chat(peer)
                return True, chat.id, getattr(chat, "title", str(peer)), "Успешно вступил в группу"
            except UserAlreadyParticipant:
                try:
                    chat = await client.get_chat(peer)
                    return True, chat.id, getattr(chat, "title", str(peer)), "Уже состоит в группе"
                except Exception:
                    return True, peer, str(peer), "Уже состоит в группе"
            except FloodWait as e:
                return False, None, str(peer), f"FloodWait: подождите {e.value} сек."
            except RPCError as e:
                return False, None, str(peer), f"Ошибка Telegram: {e.MESSAGE or str(e)}"
            except Exception as e:
                return False, None, str(peer), f"Ошибка: {str(e)}"

    @classmethod
    async def join_all_accounts_to_chat(
        cls,
        raw_target: str,
    ) -> Tuple[int, int, str, List[Dict[str, Any]]]:
        """
        Принудительно присоединяет все активные MTProto-аккаунты к указанной группе или каналу.
        Возвращает (success_count, error_count, resolved_title, details_list).
        """
        async with get_session() as session:
            stmt = select(
                UserSession.user_id,
                UserSession.session_string,
                UserSession.phone_number,
                UserSession.first_name,
            ).where(
                (UserSession.is_active == True) | (UserSession.is_active == 1) | (UserSession.is_active.is_(None))
            ).order_by(UserSession.created_at.asc())
            rows = (await session.execute(stmt)).all()

        if not rows:
            return 0, 0, raw_target, []

        success_count = 0
        error_count = 0
        details = []
        resolved_title = raw_target

        for idx, (uid, session_string, phone, name) in enumerate(rows):
            client = cls.create_client(session_string, name=f"bulk_join_{uid}_{hash(session_string)}")
            try:
                await client.connect()
                ok, peer_id, title, status_msg = await cls.join_chat_flexible(client, raw_target)
                if title and title != "Приватная группа":
                    resolved_title = title
                elif resolved_title == raw_target and title:
                    resolved_title = title

                if ok:
                    success_count += 1
                    details.append({
                        "user_id": uid,
                        "phone": phone,
                        "name": name or "Без имени",
                        "success": True,
                        "status": status_msg or "Вступил",
                    })
                else:
                    error_count += 1
                    details.append({
                        "user_id": uid,
                        "phone": phone,
                        "name": name or "Без имени",
                        "success": False,
                        "status": status_msg or "Ошибка",
                    })

                await client.disconnect()
            except Exception as e:
                error_count += 1
                details.append({
                    "user_id": uid,
                    "phone": phone,
                    "name": name or "Без имени",
                    "success": False,
                    "status": str(e),
                })
            finally:
                if client.is_connected:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass

            await asyncio.sleep(1.0)

        return success_count, error_count, resolved_title, details
