import asyncio
import logging
import re
from datetime import datetime
from typing import Dict, Optional, Tuple
from pyrogram import Client, filters
from pyrogram.types import Message as PyroMessage
from pyrogram.errors import FloodWait, RPCError
from aiogram import Bot
from sqlalchemy import select, update

from bot.config import config
from bot.database.db import get_session
from bot.database.models import UserSession, AnonChatSettings
from bot.services.telegram_client import TelegramClientService

logger = logging.getLogger(__name__)

ANON_BOT_USERNAME = "AnonRuBot"

class AnonChatWorkerService:
    """
    Фоновый сервис для автоматизированного взаимодействия с @AnonRuBot:
    - Поиск собеседника (/search или /next)
    - Отправка рекламного сообщения при статусе «Собеседник найден»
    - Детект лидов (при получении '+' или триггера) с мгновенным оповещением администратора
    - Авто-скип и бесконечный цикл поиска
    """

    def __init__(self):
        self._tasks: Dict[int, asyncio.Task] = {}
        self._clients: Dict[int, Client] = {}
        self._bot: Optional[Bot] = None

    def is_running(self, user_id: int) -> bool:
        task = self._tasks.get(user_id)
        return task is not None and not task.done()

    async def start_worker(self, user_id: int, bot: Optional[Bot] = None) -> Tuple[bool, str]:
        """
        Запуск фонового воркера для анонимного чата.
        """
        if bot:
            self._bot = bot

        if self.is_running(user_id):
            return True, "Авто-чат уже запущен."

        # Проверяем наличие сессии и настроек
        async with get_session() as session:
            sess_stmt = select(UserSession).where(UserSession.user_id == user_id, UserSession.is_active == True)
            user_sess = (await session.execute(sess_stmt)).scalar_one_or_none()
            if not user_sess:
                return False, "❌ Аккаунт не подключен! Сначала привяжите Telegram-аккаунт в меню «📱 Мой аккаунт»."

            set_stmt = select(AnonChatSettings).where(AnonChatSettings.user_id == user_id)
            settings = (await session.execute(set_stmt)).scalar_one_or_none()
            if not settings:
                settings = AnonChatSettings(user_id=user_id, is_running=True)
                session.add(settings)
            else:
                settings.is_running = True

        task = asyncio.create_task(self._worker_loop(user_id))
        self._tasks[user_id] = task
        logger.info(f"AnonChat worker started for user {user_id}")
        return True, "▶️ Авто-чат в @AnonRuBot успешно запущен!"

    async def stop_worker(self, user_id: int, reason: Optional[str] = None) -> Tuple[bool, str]:
        """
        Остановка фонового воркера анонимного чата.
        """
        task = self._tasks.pop(user_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Error awaiting cancelled anon worker for user {user_id}: {e}")

        # Отключаем клиента
        client = self._clients.pop(user_id, None)
        if client and client.is_connected:
            try:
                await client.disconnect()
            except Exception:
                pass

        async with get_session() as session:
            await session.execute(
                update(AnonChatSettings)
                .where(AnonChatSettings.user_id == user_id)
                .values(is_running=False)
            )

        logger.info(f"AnonChat worker stopped for user {user_id}. Reason: {reason or 'User requested'}")
        return True, "⏸ Авто-чат остановлен."

    async def restore_active_workers(self, bot: Bot):
        """
        Восстановление всех запущенных воркеров анонимного чата при перезапуске бота.
        """
        self._bot = bot
        logger.info("Restoring active AnonChat workers from database...")
        async with get_session() as session:
            stmt = select(AnonChatSettings.user_id).where(AnonChatSettings.is_running == True)
            running_users = (await session.execute(stmt)).scalars().all()

        restored = 0
        for uid in running_users:
            if not self.is_running(uid):
                task = asyncio.create_task(self._worker_loop(uid))
                self._tasks[uid] = task
                restored += 1

        logger.info(f"Successfully restored {restored} active AnonChat workers.")

    async def _worker_loop(self, user_id: int):
        """
        Основной асинхронный цикл работы с @AnonRuBot через Pyrogram.
        """
        logger.info(f"Starting AnonChat worker loop for user {user_id}")

        async with get_session() as session:
            sess_stmt = select(UserSession).where(UserSession.user_id == user_id, UserSession.is_active == True)
            user_sess = (await session.execute(sess_stmt)).scalar_one_or_none()

        if not user_sess:
            logger.error(f"Cannot start anon worker: no session for user {user_id}")
            return

        client = TelegramClientService.create_client(user_sess.session_string, name=f"anon_{user_id}")
        self._clients[user_id] = client

        try:
            await client.connect()
            logger.info(f"AnonChat client connected for user {user_id}")

            me = await client.get_me()
            my_id = me.id

            # Разрешаем peer для @AnonRuBot
            try:
                anon_peer = await client.resolve_peer(ANON_BOT_USERNAME)
            except Exception as e:
                logger.error(f"Failed to resolve @{ANON_BOT_USERNAME}: {e}")
                await self._notify_admin(f"⚠️ Ошибка поиска бота @{ANON_BOT_USERNAME}: {e}")
                return

            # Инициализируем last_msg_id последним сообщением в чате, чтобы не считывать старую историю
            last_msg_id = 0
            try:
                async for msg in client.get_chat_history(ANON_BOT_USERNAME, limit=1):
                    last_msg_id = msg.id
            except Exception:
                pass

            state = "IDLE"  # IDLE, SEARCHING, CHATTING, LEAD_ACTIVE
            chat_started_time = 0
            search_started_time = 0
            has_sent_offer = False

            # Инициализируем диалог — отправляем /search
            await client.send_message(ANON_BOT_USERNAME, "/search")
            state = "SEARCHING"
            search_started_time = asyncio.get_event_loop().time()
            await asyncio.sleep(2)

            while True:
                # 1. Загружаем свежие настройки из БД
                async with get_session() as session:
                    set_stmt = select(AnonChatSettings).where(AnonChatSettings.user_id == user_id)
                    settings = (await session.execute(set_stmt)).scalar_one_or_none()
                    if not settings or not settings.is_running:
                        logger.info(f"AnonChat loop stopping by DB flag for user {user_id}")
                        break

                    offer_text = settings.message_text or "Привет! Интересует? Напиши +"
                    trigger_sym = (settings.trigger_symbol or "+").strip().lower()
                    delay_sec = max(1, settings.delay_seconds)

                # 2. Получаем новые сообщения от @AnonRuBot
                try:
                    # Считываем батчем и сортируем по возрастанию ID (от старых к новым)
                    batch = []
                    async for msg in client.get_chat_history(ANON_BOT_USERNAME, limit=8):
                        if msg.id <= last_msg_id:
                            break
                        batch.append(msg)

                    batch.sort(key=lambda m: m.id)

                    for msg in batch:
                        last_msg_id = max(last_msg_id, msg.id)

                        # Если это наше сообщение (исходящее или от нашего user_id) — отключаем авто-скип и пропускаем
                        if msg.outgoing or (msg.from_user and msg.from_user.id == my_id):
                            chat_started_time = 0
                            continue

                        text = (msg.text or "").strip()
                        lower_text = text.lower()

                        # Если текст полностью или частично совпадает с нашим оффером — это наше сообщение
                        if text == offer_text or lower_text == offer_text.lower().strip() or "отправь +" in lower_text or "пишите +" in lower_text:
                            chat_started_time = 0
                            continue

                        # 0. Проверяем сообщение о БАНЕ / временном ограничении в @AnonRuBot!
                        is_ban_msg = (
                            "временно ограничили" in lower_text
                            or "до разблокировки осталось" in lower_text
                            or "за нарушение правил анонимного чата" in lower_text
                        )
                        if is_ban_msg:
                            logger.warning(f"🚫 BAN DETECTED for user {user_id} in @AnonRuBot: {text}")
                            # Извлекаем срок бана
                            time_match = re.search(r'до разблокировки осталось:?[\s]*([^\n\r]+)', text, re.IGNORECASE)
                            remaining_time = time_match.group(1).strip() if time_match else "до 24 часов"

                            # Останавливаем статус в базе данных
                            async with get_session() as session:
                                await session.execute(
                                    update(AnonChatSettings)
                                    .where(AnonChatSettings.user_id == user_id)
                                    .values(is_running=False)
                                )

                            acc_name = f"{user_sess.first_name or ''} {user_sess.username or ''}".strip() or str(user_id)
                            ban_alert = (
                                f"🚨 <b>ВНИМАНИЕ: АККАУНТ ВРЕМЕННО ЗАБАНЕН В @AnonRuBot!</b>\n\n"
                                f"👤 <b>Аккаунт:</b> {acc_name} (<code>{user_sess.phone_number}</code>)\n"
                                f"⏳ <b>До разблокировки осталось:</b> <code>{remaining_time}</code>\n"
                                f"────────────────────\n"
                                f"📄 <b>Сообщение от бота:</b>\n"
                                f"<i>{text[:350]}</i>\n\n"
                                f"⚠️ <i>Авто-чат для этого аккаунта автоматически приостановлен до снятия бана.</i>"
                            )
                            await self._notify_admin(ban_alert)
                            return

                        # Проверяем системные статусы от @AnonRuBot
                        is_match_found = "собеседник найден" in lower_text
                        is_partner_left = "собеседник закончил" in lower_text or "закончил с вами связь" in lower_text or "диалог остановлен" in lower_text
                        is_search_prompt = "напишите /search" in lower_text or "ищем собеседника" in lower_text or "для улучшения поиска" in lower_text
                        is_feedback_or_ad = "оставьте мнение" in lower_text or "больше собеседников" in lower_text or "неадекватно общался" in lower_text or "начать диалог" in lower_text

                        # А) Собеседник найден!
                        if is_match_found:
                            state = "CHATTING"
                            has_sent_offer = False
                            chat_started_time = asyncio.get_event_loop().time()
                            search_started_time = 0

                            # Фиксируем статистику найденных диалогов
                            async with get_session() as session:
                                await session.execute(
                                    update(AnonChatSettings)
                                    .where(AnonChatSettings.user_id == user_id)
                                    .values(total_dialogs=AnonChatSettings.total_dialogs + 1)
                                )

                            logger.info(f"User {user_id} matched with partner in @AnonRuBot. Sending offer...")
                            await asyncio.sleep(1.5)  # естественная пауза 1.5 сек

                            if not has_sent_offer:
                                await client.send_message(ANON_BOT_USERNAME, offer_text)
                                has_sent_offer = True
                            continue

                        # Б) Собеседник отключился / скипнул
                        elif is_partner_left:
                            logger.info(f"Partner disconnected for user {user_id}. Searching next...")
                            state = "SEARCHING"
                            has_sent_offer = False
                            chat_started_time = 0
                            search_started_time = asyncio.get_event_loop().time()
                            await asyncio.sleep(delay_sec)
                            await client.send_message(ANON_BOT_USERNAME, "/next")
                            continue

                        # В) Бот предлагает начать поиск
                        elif is_search_prompt and state != "CHATTING":
                            state = "SEARCHING"
                            has_sent_offer = False
                            chat_started_time = 0
                            search_started_time = asyncio.get_event_loop().time()
                            await asyncio.sleep(delay_sec)
                            await client.send_message(ANON_BOT_USERNAME, "/search")
                            continue

                        # Г) Пропускаем системные рекламы и опросы от самого AnonRuBot
                        elif is_feedback_or_ad:
                            continue

                        # Д) Реальное входящее сообщение от СОБЕСЕДНИКА!
                        elif state in ("CHATTING", "LEAD_ACTIVE") and not is_match_found and not is_partner_left and not is_search_prompt:
                            # Как только собеседник ответил хоть что-то — отключаем авто-скип /next!
                            chat_started_time = 0

                            # Проверяем наличие триггера (например, '+')
                            is_trigger_hit = False
                            if trigger_sym == "+":
                                is_trigger_hit = "+" in text or text.strip() == "+"
                            else:
                                is_trigger_hit = trigger_sym in lower_text

                            if is_trigger_hit:
                                state = "LEAD_ACTIVE"  # Фиксируем активный диалог с лидом
                                logger.info(f"🎯 LEAD CAPTURED for user {user_id}! Partner replied: {text}")
                                now = datetime.utcnow()

                                # Обновляем статистику лидов
                                async with get_session() as session:
                                    await session.execute(
                                        update(AnonChatSettings)
                                        .where(AnonChatSettings.user_id == user_id)
                                        .values(
                                            total_leads=AnonChatSettings.total_leads + 1,
                                            last_lead_at=now
                                        )
                                    )

                                # Формируем и отправляем оповещение администратору
                                acc_name = f"{user_sess.first_name or ''} {user_sess.username or ''}".strip() or str(user_id)
                                alert_text = (
                                    f"🎯 <b>НОВЫЙ ЛИД ИЗ АНОНИМНОГО ЧАТА (@AnonRuBot)!</b>\n\n"
                                    f"👤 <b>Аккаунт:</b> {acc_name} (<code>{user_sess.phone_number}</code>)\n"
                                    f"💬 <b>Ответ собеседника:</b>\n"
                                    f"<blockquote>{text}</blockquote>\n"
                                    f"🕒 <b>Время:</b> {now.strftime('%H:%M:%S')}\n"
                                    f"────────────────────\n"
                                    f"📄 <b>Наш оффер:</b>\n"
                                    f"<i>{offer_text[:200]}</i>"
                                )
                                await self._notify_admin(alert_text)

                except FloodWait as fw:
                    logger.warning(f"FloodWait in AnonChat loop for user {user_id}: {fw.value}s")
                    await asyncio.sleep(fw.value)
                except RPCError as rpc:
                    logger.warning(f"RPCError in AnonChat loop for user {user_id}: {rpc}")
                except Exception as loop_err:
                    logger.error(f"Error checking AnonChat messages for user {user_id}: {loop_err}")

                # Watchdog поиска: если поиск завис более чем на 35 секунд без ответа — отправляем /search повторно
                if state == "SEARCHING" and search_started_time > 0:
                    if (asyncio.get_event_loop().time() - search_started_time) > 35:
                        logger.info(f"Search watchdog timeout (35s) for user {user_id}. Re-triggering /search...")
                        search_started_time = asyncio.get_event_loop().time()
                        await client.send_message(ANON_BOT_USERNAME, "/search")

                # Таймаут неактивности: скипаем диалог ТОЛЬКО если собеседник вообще ничего не ответил за 45 секунд
                if state == "CHATTING" and chat_started_time > 0:
                    elapsed = asyncio.get_event_loop().time() - chat_started_time
                    if elapsed > 45:
                        logger.info(f"Inactivity timeout (45s, no reply from partner) for user {user_id}. Skipping to next...")
                        state = "SEARCHING"
                        has_sent_offer = False
                        chat_started_time = 0
                        search_started_time = asyncio.get_event_loop().time()
                        await client.send_message(ANON_BOT_USERNAME, "/next")

                await asyncio.sleep(2)  # опрос каждые 2 секунды

        except asyncio.CancelledError:
            logger.info(f"AnonChat worker cancelled for user {user_id}")
        except Exception as e:
            logger.error(f"Unexpected error in AnonChat worker for user {user_id}: {e}", exc_info=True)
            await self._notify_admin(f"⚠️ Ошибка в работе авто-чата AnonRuBot: {str(e)}")
        finally:
            if client.is_connected:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            self._clients.pop(user_id, None)

    async def _notify_admin(self, text: str):
        """
        Отправка уведомления администратору бота.
        """
        if self._bot and config.ADMIN_ID:
            try:
                await self._bot.send_message(
                    chat_id=config.ADMIN_ID,
                    text=text,
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Failed to send admin alert: {e}")

# Синглтон сервиса
anon_chat_service = AnonChatWorkerService()
