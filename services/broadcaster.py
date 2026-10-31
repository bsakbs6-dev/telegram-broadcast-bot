import asyncio
import logging
import random
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, List
from aiogram import Bot
from sqlalchemy import select, update

from bot.config import config
from bot.database.db import get_session
from bot.database.models import (
    BroadcastSettings,
    BroadcastMessage,
    TargetChannel,
    UserSession,
)
from bot.services.telegram_client import TelegramClientService

logger = logging.getLogger(__name__)

class BroadcasterService:
    """
    Менеджер фоновых циклов рассылки для всех пользователей.
    Управляет жизненным циклом фоновых задач asyncio.
    """

    def __init__(self):
        self._tasks: Dict[int, asyncio.Task] = {}
        self._bot: Optional[Bot] = None

    def set_bot(self, bot: Bot):
        """
        Установка экземпляра бота для отправки служебных уведомлений пользователям.
        """
        self._bot = bot

    def is_running(self, user_id: int) -> bool:
        """
        Проверяет, работает ли задача рассылки в данный момент в памяти.
        """
        task = self._tasks.get(user_id)
        return task is not None and not task.done()

    async def start_broadcast(self, user_id: int) -> Tuple[bool, str]:
        """
        Запуск циклической рассылки для конкретного пользователя.
        """
        if self.is_running(user_id):
            return False, "Рассылка уже запущена!"

        # Проверяем готовность в БД (наличие сессии, сообщений, каналов)
        async with get_session() as session:
            # 1. Сессия
            sess_stmt = select(UserSession).where(UserSession.user_id == user_id, UserSession.is_active == True)
            user_session = (await session.execute(sess_stmt)).scalar_one_or_none()
            if not user_session:
                return False, "❌ Аккаунт не подключен или сессия не активна. Сначала авторизуйтесь в разделе «🔑 Подключить аккаунт»."

            # 2. Пул сообщений (минимум 2 сообщения по ТЗ)
            msg_stmt = select(BroadcastMessage).where(BroadcastMessage.user_id == user_id)
            messages = (await session.execute(msg_stmt)).scalars().all()
            if len(messages) < 2:
                return False, f"❌ Для запуска необходимо настроить от 2 до 4 текстов сообщений. Сейчас добавлено: {len(messages)}."

            # 3. Выбранные каналы
            chan_stmt = select(TargetChannel).where(TargetChannel.user_id == user_id, TargetChannel.is_selected == True)
            channels = (await session.execute(chan_stmt)).scalars().all()
            if not channels:
                return False, "❌ Не выбрано ни одного канала для рассылки. Перейдите в «📢 Выбор каналов» и отметьте каналы."

            # Обновляем статус в БД
            await session.execute(
                update(BroadcastSettings)
                .where(BroadcastSettings.user_id == user_id)
                .values(is_running=True, last_error=None)
            )

        # Создаем и запускаем фоновую задачу
        task = asyncio.create_task(self._broadcast_worker(user_id))
        self._tasks[user_id] = task
        logger.info(f"Broadcast task started for user {user_id}")
        return True, "▶️ Рассылка успешно запущена в фоновом режиме!"

    async def stop_broadcast(self, user_id: int, reason: Optional[str] = None) -> Tuple[bool, str]:
        """
        Остановка фоновой рассылки пользователя.
        """
        task = self._tasks.pop(user_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Error awaiting cancelled task for user {user_id}: {e}")

        # Обновляем статус в базе данных
        async with get_session() as session:
            await session.execute(
                update(BroadcastSettings)
                .where(BroadcastSettings.user_id == user_id)
                .values(is_running=False, next_run_at=None, last_error=reason)
            )

        logger.info(f"Broadcast task stopped for user {user_id}. Reason: {reason or 'User requested'}")
        return True, "⏸ Рассылка остановлена."

    async def restore_active_broadcasts(self, bot: Bot):
        """
        Восстановление всех запущенных рассылок при перезапуске сервера.
        """
        self._bot = bot
        logger.info("Restoring active broadcasts from database...")
        async with get_session() as session:
            stmt = select(BroadcastSettings.user_id).where(BroadcastSettings.is_running == True)
            running_users = (await session.execute(stmt)).scalars().all()

        restored_count = 0
        for uid in running_users:
            if not self.is_running(uid):
                task = asyncio.create_task(self._broadcast_worker(uid))
                self._tasks[uid] = task
                restored_count += 1

        logger.info(f"Successfully restored {restored_count} active broadcast tasks.")

    async def _broadcast_worker(self, user_id: int):
        """
        Основной рабочий цикл рассылки для пользователя.
        """
        logger.info(f"Starting broadcast worker loop for user {user_id}")
        rotation_counter = 0

        while True:
            try:
                # 1. Загружаем свежие данные из БД
                async with get_session() as session:
                    # Проверяем флаг работы
                    set_stmt = select(BroadcastSettings).where(BroadcastSettings.user_id == user_id)
                    settings = (await session.execute(set_stmt)).scalar_one_or_none()
                    if not settings or not settings.is_running:
                        logger.info(f"Broadcast stopped in DB for user {user_id}")
                        break

                    # Сессия
                    sess_stmt = select(UserSession).where(UserSession.user_id == user_id, UserSession.is_active == True)
                    user_session = (await session.execute(sess_stmt)).scalar_one_or_none()
                    if not user_session:
                        logger.warning(f"No active session found for user {user_id}. Stopping broadcast.")
                        await self._notify_user(user_id, "⚠️ Рассылка остановлена: сессия вашего Telegram-аккаунта не найдена или неактивна.")
                        break

                    # Тексты сообщений
                    msg_stmt = select(BroadcastMessage).where(BroadcastMessage.user_id == user_id)
                    messages = (await session.execute(msg_stmt)).scalars().all()
                    if len(messages) < 2:
                        logger.warning(f"User {user_id} has less than 2 messages. Stopping broadcast.")
                        await self._notify_user(user_id, "⚠️ Рассылка остановлена: в пуле сообщений должно быть минимум 2 текста.")
                        break

                    # Выбранные каналы
                    chan_stmt = select(TargetChannel).where(TargetChannel.user_id == user_id, TargetChannel.is_selected == True)
                    channels = (await session.execute(chan_stmt)).scalars().all()
                    if not channels:
                        logger.warning(f"User {user_id} has no selected channels. Stopping broadcast.")
                        await self._notify_user(user_id, "⚠️ Рассылка остановлена: список выбранных каналов пуст.")
                        break

                    session_string = user_session.session_string
                    interval_minutes = max(1, settings.interval_minutes)
                    jitter_min = settings.random_jitter_min
                    jitter_max = settings.random_jitter_max
                    rotation_mode = settings.rotation_mode

                # 2. Выполняем один круг рассылки
                logger.info(f"Executing broadcast round for user {user_id} across {len(channels)} channels...")
                client = TelegramClientService.create_client(session_string, name=f"worker_{user_id}")

                client_connected = False
                round_sent = 0
                round_errors = 0

                try:
                    await client.connect()
                    client_connected = True

                    for channel in channels:
                        # Проверяем отмену перед каждой отправкой
                        await asyncio.sleep(0.01)

                        # Выбираем текст в зависимости от режима ротации
                        if rotation_mode == "round_robin":
                            selected_msg = messages[rotation_counter % len(messages)]
                            rotation_counter += 1
                        else:  # 'random'
                            selected_msg = random.choice(messages)

                        # Безопасный Random Jitter (пауза между отправками)
                        jitter_delay = random.uniform(jitter_min, jitter_max)
                        await asyncio.sleep(jitter_delay)

                        # Отправка сообщения
                        success, status = await TelegramClientService.send_broadcast_message(
                            client=client,
                            chat_id=channel.chat_id,
                            text=selected_msg.text,
                        )

                        # Обновляем статистику в БД
                        async with get_session() as db_sess:
                            if success:
                                round_sent += 1
                                await db_sess.execute(
                                    update(TargetChannel)
                                    .where(TargetChannel.id == channel.id)
                                    .values(last_status="ok", last_sent_at=datetime.utcnow())
                                )
                                await db_sess.execute(
                                    update(BroadcastSettings)
                                    .where(BroadcastSettings.user_id == user_id)
                                    .values(total_sent=BroadcastSettings.total_sent + 1)
                                )
                            else:
                                round_errors += 1
                                await db_sess.execute(
                                    update(TargetChannel)
                                    .where(TargetChannel.id == channel.id)
                                    .values(last_status=status)
                                )
                                await db_sess.execute(
                                    update(BroadcastSettings)
                                    .where(BroadcastSettings.user_id == user_id)
                                    .values(total_errors=BroadcastSettings.total_errors + 1)
                                )

                                # Если критическая ошибка сессии
                                if status == "session_revoked":
                                    await db_sess.execute(
                                        update(UserSession)
                                        .where(UserSession.user_id == user_id)
                                        .values(is_active=False)
                                    )
                                    await self._notify_user(user_id, "❌ Сессия аккаунта была отозвана Telegram. Рассылка прекращена.")
                                    return

                except asyncio.CancelledError:
                    raise
                except Exception as round_err:
                    logger.error(f"Error during broadcast round for user {user_id}: {round_err}", exc_info=True)
                finally:
                    if client_connected and client.is_connected:
                        try:
                            await client.disconnect()
                        except Exception:
                            pass

                # 3. Фиксируем завершение круга и время следующего запуска
                now = datetime.utcnow()
                sleep_seconds = max(30, int(float(interval_minutes) * 60))
                next_run = now + timedelta(seconds=sleep_seconds)

                async with get_session() as session:
                    await session.execute(
                        update(BroadcastSettings)
                        .where(BroadcastSettings.user_id == user_id)
                        .values(
                            last_run_at=now,
                            next_run_at=next_run,
                        )
                    )

                logger.info(
                    f"Round finished for user {user_id}. Sent: {round_sent}, Errors: {round_errors}. "
                    f"Next run at {next_run.strftime('%H:%M:%S')} (in {sleep_seconds}s)."
                )

                # 4. Ожидание следующего круга с поддержкой прерывания
                step = 2  # проверяем статус каждые 2 секунды
                while sleep_seconds > 0:
                    await asyncio.sleep(min(step, sleep_seconds))
                    sleep_seconds -= step

                    # Проверяем, не была ли рассылка остановлена в БД
                    async with get_session() as session:
                        set_stmt = select(BroadcastSettings.is_running).where(BroadcastSettings.user_id == user_id)
                        is_still_running = (await session.execute(set_stmt)).scalar_one_or_none()
                        if not is_still_running:
                            logger.info(f"Broadcast flag set to False for user {user_id}. Exiting loop.")
                            return

            except asyncio.CancelledError:
                logger.info(f"Broadcast worker cancelled for user {user_id}")
                break
            except Exception as e:
                logger.error(f"Unexpected error in broadcast worker for user {user_id}: {e}", exc_info=True)
                await asyncio.sleep(10)  # пауза перед повторной попыткой при сбое

        # Завершающая очистка флага
        try:
            async with get_session() as session:
                await session.execute(
                    update(BroadcastSettings)
                    .where(BroadcastSettings.user_id == user_id)
                    .values(is_running=False, next_run_at=None)
                )
        except Exception:
            pass

    async def _notify_user(self, user_id: int, text: str):
        """
        Отправка уведомления пользователю в чат с ботом.
        """
        if self._bot:
            try:
                await self._bot.send_message(chat_id=user_id, text=text)
            except Exception as e:
                logger.debug(f"Failed to send notification to user {user_id}: {e}")

# Синглтон сервиса
broadcaster = BroadcasterService()
