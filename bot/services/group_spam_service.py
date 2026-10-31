import asyncio
import logging
import re
import time
from typing import Dict, List, Optional, Tuple, Any
from pyrogram import Client, raw, utils
from pyrogram.errors import (
    FloodWait,
    SlowmodeWait,
    ChatWriteForbidden,
    UserBannedInChannel,
    ChannelPrivate,
    PeerFlood,
    UserDeactivated,
    AuthKeyUnregistered,
    SessionRevoked,
    UserAlreadyParticipant,
    InviteRequestSent,
    RPCError,
)
from sqlalchemy import select
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.config import config
from bot.database.db import get_session
from bot.database.models import UserSession
from bot.services.telegram_client import TelegramClientService

logger = logging.getLogger(__name__)

class GroupSpamService:
    """
    Сервис массовой отправки сообщений (спам/рейд) в указанную группу со всех привязанных аккаунтов:
    - Автоматическое вступление в публичные и приватные группы (t.me/..., t.me/+..., ID)
    - Параллельное удержание MTProto подключений для максимальной скорости (без задержек на повторный коннект)
    - Поддержка отправки «без КД» (турбо-режим) или с минимальным/настраиваемым КД
    - Равномерное чередование аккаунтов (round-robin) для защиты от спам-фильтров Telegram
    - Умный учет Slowmode (медленного режима чата) и FloodWait для каждого аккаунта
    - Live-обновление прогресса и кнопка экстренной остановки в админке
    """

    def __init__(self):
        self._running_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._target_title: str = "Группа"
        self._target_raw: str = ""
        self._active_clients: List[Client] = []

    def is_running(self) -> bool:
        """
        Проверка: выполняется ли атака/отправка в данный момент.
        """
        return self._running_task is not None and not self._running_task.done()

    def stop(self):
        """
        Экстренная остановка отправки.
        """
        self._stop_event.set()

    @staticmethod
    async def resolve_and_join_chat(client: Client, raw_target: str) -> Tuple[Any, str]:
        """
        Разрешает целевой чат и вступает в него через MTProto, если аккаунт еще не состоит в нем.
        Возвращает (chat_peer, chat_title).
        """
        target = raw_target.strip()
        invite_match = re.search(r"(?:t\.me/joinchat/|t\.me/\+)([a-zA-Z0-9_-]+)", target)

        if invite_match:
            invite_hash = invite_match.group(1)
            full_invite = f"https://t.me/+{invite_hash}"
            try:
                chat = await client.join_chat(full_invite)
                title = getattr(chat, "title", None) or "Приватная группа"
                return chat.id, title
            except UserAlreadyParticipant:
                try:
                    res = await client.invoke(raw.functions.messages.CheckChatInvite(hash=invite_hash))
                    if isinstance(res, raw.types.chat_invite_already.ChatInviteAlready):
                        raw_chat = getattr(res, "chat", None)
                        cid = getattr(raw_chat, "id", None)
                        title = getattr(raw_chat, "title", "Приватная группа")
                        if cid:
                            return utils.get_channel_id(cid), title
                except Exception as e:
                    logger.warning(f"CheckChatInvite check failed: {e}")
                return full_invite, "Приватная группа"
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
                return chat.id, getattr(chat, "title", str(peer))
            except UserAlreadyParticipant:
                try:
                    chat = await client.get_chat(peer)
                    return chat.id, getattr(chat, "title", str(peer))
                except Exception:
                    return peer, str(peer)

    async def start_spam(
        self,
        target_group: str,
        message_text: str,
        count: int,
        count_mode: str,  # "total" или "per_account"
        delay: float,     # в секундах (0.05, 0.3, 1.0 и т.д.)
        bot: Bot,
        admin_chat_id: int,
        status_message_id: int,
    ) -> bool:
        """
        Запуск фоновой задачи массовой отправки сообщений в группу.
        """
        if self.is_running():
            return False

        self._stop_event.clear()
        self._target_raw = target_group

        self._running_task = asyncio.create_task(
            self._spam_worker(
                target_group=target_group,
                message_text=message_text,
                count=count,
                count_mode=count_mode,
                delay=delay,
                bot=bot,
                admin_chat_id=admin_chat_id,
                status_message_id=status_message_id,
            )
        )
        return True

    async def _spam_worker(
        self,
        target_group: str,
        message_text: str,
        count: int,
        count_mode: str,
        delay: float,
        bot: Bot,
        admin_chat_id: int,
        status_message_id: int,
    ):
        """
        Основной рабочий цикл отправки сообщений.
        """
        # 1. Загружаем активные сессии из БД
        async with get_session() as session:
            stmt = select(UserSession).where(
                (UserSession.is_active == True) | (UserSession.is_active == 1) | (UserSession.is_active.is_(None))
            ).order_by(UserSession.created_at.asc())
            sessions = (await session.execute(stmt)).scalars().all()

        if not sessions:
            try:
                await bot.edit_message_text(
                    chat_id=admin_chat_id,
                    message_id=status_message_id,
                    text="❌ <b>Ошибка:</b> В базе данных нет активных привязанных Telegram-аккаунтов!",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="⬅️ В панель админа", callback_data="menu_admin")]
                    ]),
                    parse_mode="HTML"
                )
            except Exception:
                pass
            return

        # 2. Подключаем клиенты и вступаем в целевой чат
        try:
            await bot.edit_message_text(
                chat_id=admin_chat_id,
                message_id=status_message_id,
                text=(
                    f"⏳ <b>Подключение аккаунтов к группе...</b>\n\n"
                    f"📍 Цель: <code>{target_group}</code>\n"
                    f"📱 Найдено аккаунтов: <b>{len(sessions)}</b>\n\n"
                    f"<i>Пожалуйста, подождите, аккаунты вступают в чат...</i>"
                ),
                parse_mode="HTML"
            )
        except Exception:
            pass

        workers = []
        group_title = target_group

        for sess in sessions:
            if self._stop_event.is_set():
                break

            client = TelegramClientService.create_client(
                sess.session_string,
                name=f"spam_{sess.user_id}_{hash(sess.session_string)}"
            )
            try:
                await client.connect()
                self._active_clients.append(client)

                chat_peer, title = await self.resolve_and_join_chat(client, target_group)
                if title:
                    group_title = title

                workers.append({
                    "client": client,
                    "session": sess,
                    "chat_peer": chat_peer,
                    "sent": 0,
                    "errors": 0,
                    "slowmode_until": 0.0,
                    "flood_until": 0.0,
                    "banned": False,
                })
            except (AuthKeyUnregistered, UserDeactivated, SessionRevoked):
                logger.warning(f"Session {sess.phone_number} is revoked/deactivated during group join.")
            except InviteRequestSent:
                logger.warning(f"Session {sess.phone_number} sent join request (group requires approval).")
            except Exception as join_err:
                logger.warning(f"Error joining chat for {sess.phone_number}: {join_err}")

        self._target_title = group_title

        if not workers:
            try:
                await bot.edit_message_text(
                    chat_id=admin_chat_id,
                    message_id=status_message_id,
                    text=(
                        f"❌ <b>Не удалось подключить аккаунты к группе!</b>\n\n"
                        f"📍 Цель: <code>{target_group}</code>\n"
                        f"⚠️ Возможные причины: неверная ссылка, закрытая группа с заявками, "
                        f"аккаунты забанены в этой группе или нет прав на вступление."
                    ),
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="⬅️ В панель админа", callback_data="menu_admin")]
                    ]),
                    parse_mode="HTML"
                )
            except Exception:
                pass
            await self._cleanup_clients()
            return

        # 3. Расчет целевого количества сообщений
        if count_mode == "per_account":
            target_total = count * len(workers)
            mode_desc = f"по {count} с каждого из {len(workers)} аккаунтов"
        else:
            target_total = count
            mode_desc = f"{target_total} сообщений всего"

        delay_desc = f"{delay} сек." if delay > 0 else "Без КД (турбо)"
        text_preview = message_text[:100] + ("..." if len(message_text) > 100 else "")

        stop_markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛑 Остановить отправку", callback_data="admin_spam_stop")]
        ])

        try:
            await bot.edit_message_text(
                chat_id=admin_chat_id,
                message_id=status_message_id,
                text=(
                    f"⚡️ <b>Идет отправка сообщений в группу...</b>\n\n"
                    f"📍 <b>Группа:</b> {group_title}\n"
                    f"💬 <b>Текст:</b> <i>«{text_preview}»</i>\n"
                    f"🔢 <b>План:</b> <b>0 / {target_total}</b> ({mode_desc})\n"
                    f"⏱ <b>КД:</b> {delay_desc}\n"
                    f"📱 <b>Аккаунтов в работе:</b> {len(workers)}\n\n"
                    f"<i>Нажмите кнопку ниже в любой момент для остановки:</i>"
                ),
                reply_markup=stop_markup,
                parse_mode="HTML"
            )
        except Exception:
            pass

        # 4. Основной цикл рассылки
        sent_total = 0
        error_total = 0
        worker_idx = 0
        start_time = time.time()
        last_ui_update = time.time()

        try:
            while sent_total < target_total and not self._stop_event.is_set():
                now = time.time()

                # Отбираем доступных воркеров (не забаненных и не на кулдауне)
                available = [
                    w for w in workers
                    if not w["banned"] and now >= w["slowmode_until"] and now >= w["flood_until"]
                    and (count_mode != "per_account" or w["sent"] < count)
                ]

                if not available:
                    # Проверяем, не заблокированы ли все воркеры насовсем
                    active_unbanned = [w for w in workers if not w["banned"] and (count_mode != "per_account" or w["sent"] < count)]
                    if not active_unbanned:
                        logger.warning("All workers have reached limits or are banned/finished.")
                        break

                    # Если все временно ждут slowmode/flood — спим до ближайшего
                    min_wait = min(
                        max(0.5, min(w["slowmode_until"], w["flood_until"]) - now)
                        for w in active_unbanned
                    )
                    await asyncio.sleep(min(max(min_wait, 0.5), 5.0))
                    continue

                w = available[worker_idx % len(available)]
                worker_idx += 1

                try:
                    await w["client"].send_message(
                        chat_id=w["chat_peer"],
                        text=message_text,
                        disable_web_page_preview=False,
                    )
                    w["sent"] += 1
                    sent_total += 1
                except SlowmodeWait as sm:
                    logger.info(f"SlowmodeWait for {w['session'].phone_number}: {sm.value}s")
                    w["slowmode_until"] = time.time() + sm.value
                except FloodWait as fw:
                    logger.info(f"FloodWait for {w['session'].phone_number}: {fw.value}s")
                    w["flood_until"] = time.time() + fw.value
                    w["errors"] += 1
                    error_total += 1
                except (ChatWriteForbidden, UserBannedInChannel):
                    logger.warning(f"Account {w['session'].phone_number} is forbidden/banned in group.")
                    w["banned"] = True
                    w["errors"] += 1
                    error_total += 1
                except Exception as send_err:
                    logger.warning(f"Error sending from {w['session'].phone_number}: {send_err}")
                    w["errors"] += 1
                    error_total += 1

                # Задержка (КД)
                if delay > 0:
                    await asyncio.sleep(delay)
                else:
                    # При нулевом КД минимальный yield для нормальной работы event loop
                    await asyncio.sleep(0.02)

                # Периодическое обновление статуса в сообщении Telegram (каждые 2.5 сек)
                if time.time() - last_ui_update >= 2.5:
                    last_ui_update = time.time()
                    try:
                        await bot.edit_message_text(
                            chat_id=admin_chat_id,
                            message_id=status_message_id,
                            text=(
                                f"⚡️ <b>Идет отправка сообщений в группу...</b>\n\n"
                                f"📍 <b>Группа:</b> {group_title}\n"
                                f"💬 <b>Текст:</b> <i>«{text_preview}»</i>\n"
                                f"📊 <b>Прогресс:</b> <b>{sent_total} / {target_total}</b> "
                                f"(✅ {sent_total} | ❌ {error_total})\n"
                                f"⏱ <b>КД:</b> {delay_desc}\n"
                                f"📱 <b>Аккаунтов в работе:</b> {len(workers)}\n\n"
                                f"<i>Нажмите кнопку ниже в любой момент для остановки:</i>"
                            ),
                            reply_markup=stop_markup,
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass

        finally:
            elapsed = round(time.time() - start_time, 1)
            was_stopped = self._stop_event.is_set()

            # Отключаем клиенты
            await self._cleanup_clients()

            # Итоговый отчет
            status_header = "🛑 <b>Отправка принудительно остановлена!</b>" if was_stopped else "🎉 <b>Отправка сообщений в группу завершена!</b>"
            report_lines = [
                f"{status_header}\n",
                f"📍 <b>Группа:</b> {group_title}",
                f"💬 <b>Текст:</b> <i>«{text_preview}»</i>",
                f"📊 <b>Всего отправлено:</b> <b>{sent_total} / {target_total}</b>",
                f"❌ <b>Ошибок/ограничений:</b> {error_total}",
                f"⏱ <b>Затраченное время:</b> {elapsed} сек.\n",
                f"<b>📋 Статистика по аккаунтам:</b>",
            ]

            for w in workers:
                phone = w["session"].phone_number
                name = w["session"].first_name or "Без имени"
                banned_mark = " (⛔️ мут/бан)" if w["banned"] else ""
                report_lines.append(
                    f"• <code>{phone}</code> ({name}): <b>{w['sent']}</b> сообщ.{banned_mark}"
                )

            final_text = "\n".join(report_lines)

            back_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📱 Список сессий", callback_data="admin_sessions_list")],
                [InlineKeyboardButton(text="⬅️ В панель админа", callback_data="menu_admin")]
            ])

            if len(final_text) > 4000:
                final_text = final_text[:3950] + "\n\n<i>[Список сокращен...]</i>"

            try:
                await bot.edit_message_text(
                    chat_id=admin_chat_id,
                    message_id=status_message_id,
                    text=final_text,
                    reply_markup=back_kb,
                    parse_mode="HTML"
                )
            except Exception:
                try:
                    await bot.send_message(
                        chat_id=admin_chat_id,
                        text=final_text,
                        reply_markup=back_kb,
                        parse_mode="HTML"
                    )
                except Exception as final_err:
                    logger.error(f"Failed to deliver final spam report: {final_err}")

    async def _cleanup_clients(self):
        """
        Корректно закрывает все открытые MTProto клиенты.
        """
        for client in self._active_clients:
            if client.is_connected:
                try:
                    await client.disconnect()
                except Exception:
                    pass
        self._active_clients.clear()

# Синглтон сервиса группового спама
group_spam_service = GroupSpamService()
