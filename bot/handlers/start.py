import logging
from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func

from bot.config import config
from bot.database.db import get_session
from bot.database.models import (
    UserSession,
    BroadcastSettings,
    BroadcastMessage,
    TargetChannel,
)
from bot.keyboards.main_kb import get_main_menu_kb
from bot.services.broadcaster import broadcaster

router = Router(name="start_router")
logger = logging.getLogger(__name__)

async def render_main_menu_text(user_id: int) -> str:
    """
    Формирует информационный текст главного меню бота.
    """
    async with get_session() as session:
        # Проверяем сессию
        sess_stmt = select(UserSession).where(UserSession.user_id == user_id, UserSession.is_active == True)
        user_sess = (await session.execute(sess_stmt)).scalar_one_or_none()

        # Проверяем настройки и статус рассылки
        set_stmt = select(BroadcastSettings).where(BroadcastSettings.user_id == user_id)
        settings = (await session.execute(set_stmt)).scalar_one_or_none()

        # Количество текстов в пуле
        msg_count_stmt = select(func.count(BroadcastMessage.id)).where(BroadcastMessage.user_id == user_id)
        msg_count = (await session.execute(msg_count_stmt)).scalar() or 0

        # Каналы
        total_chan_stmt = select(func.count(TargetChannel.id)).where(TargetChannel.user_id == user_id)
        total_channels = (await session.execute(total_chan_stmt)).scalar() or 0

        selected_chan_stmt = select(func.count(TargetChannel.id)).where(
            TargetChannel.user_id == user_id, TargetChannel.is_selected == True
        )
        selected_channels = (await session.execute(selected_chan_stmt)).scalar() or 0

    is_running = broadcaster.is_running(user_id) or (settings and settings.is_running)

    status_badge = "🟢 <b>Работает</b>" if is_running else "🔴 <b>Остановлена</b>"
    account_status = (
        f"✅ Подключен: <b>{user_sess.first_name or 'Пользователь'}</b> (<code>{user_sess.phone_number}</code>)"
        if user_sess else "❌ Не подключен"
    )

    interval_text = f"{settings.interval_minutes} мин" if settings else "30 мин"
    rotation_text = "Случайно" if (settings and settings.rotation_mode == "random") else "По очереди"

    text = (
        "🤖 <b>Панель управления рассылкой MTProto</b>\n\n"
        f"👤 <b>Статус аккаунта:</b> {account_status}\n"
        f"🚀 <b>Статус рассылки:</b> {status_badge}\n"
        "────────────────────\n"
        f"✉️ <b>Сообщений в пуле:</b> {msg_count} из 4 (мин. 2)\n"
        f"📢 <b>Каналов выбрано:</b> {selected_channels} из {total_channels}\n"
        f"⏱ <b>Интервал цикла:</b> {interval_text}\n"
        f"🔄 <b>Режим ротации:</b> {rotation_text}\n"
        "────────────────────\n"
        "<i>Используйте кнопки меню ниже для настройки и запуска автоматической рассылки:</i>"
    )
    return text

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """
    Обработчик команды /start.
    """
    await state.clear()
    user_id = message.from_user.id
    is_admin = (user_id == config.ADMIN_ID)

    text = await render_main_menu_text(user_id)
    kb = get_main_menu_kb(is_admin=is_admin)

    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "menu_main")
async def cb_main_menu(callback: CallbackQuery, state: FSMContext):
    """
    Возврат в главное меню через инлайн-кнопку.
    """
    await state.clear()
    user_id = callback.from_user.id
    is_admin = (user_id == config.ADMIN_ID)

    text = await render_main_menu_text(user_id)
    kb = get_main_menu_kb(is_admin=is_admin)

    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")

    await callback.answer()

@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    """
    Заглушка для некликабельных информационных кнопок пагинации.
    """
    await callback.answer()
