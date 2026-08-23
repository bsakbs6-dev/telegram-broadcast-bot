import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func

from bot.database.db import get_session
from bot.database.models import (
    BroadcastSettings,
    BroadcastMessage,
    TargetChannel,
    UserSession,
)
from bot.services.broadcaster import broadcaster

router = Router(name="control_router")
logger = logging.getLogger(__name__)

async def render_control_view(user_id: int):
    """
    Формирует экран мониторинга и кнопок управления рассылкой.
    """
    async with get_session() as session:
        # Сессия
        sess_stmt = select(UserSession).where(UserSession.user_id == user_id, UserSession.is_active == True)
        user_sess = (await session.execute(sess_stmt)).scalar_one_or_none()

        # Настройки и статистика
        set_stmt = select(BroadcastSettings).where(BroadcastSettings.user_id == user_id)
        settings = (await session.execute(set_stmt)).scalar_one_or_none()

        # Сообщения
        msg_count_stmt = select(func.count(BroadcastMessage.id)).where(BroadcastMessage.user_id == user_id)
        msg_count = (await session.execute(msg_count_stmt)).scalar() or 0

        # Каналы
        sel_chan_stmt = select(func.count(TargetChannel.id)).where(
            TargetChannel.user_id == user_id, TargetChannel.is_selected == True
        )
        selected_channels = (await session.execute(sel_chan_stmt)).scalar() or 0

    is_active = broadcaster.is_running(user_id) or (settings and settings.is_running)

    status_icon = "🟢" if is_active else "🔴"
    status_text = "<b>РАБОТАЕТ (в фоне)</b>" if is_active else "<b>ОСТАНОВЛЕНА</b>"

    last_run_str = settings.last_run_at.strftime("%Y-%m-%d %H:%M:%S") if (settings and settings.last_run_at) else "Еще не выполнялась"
    next_run_str = settings.next_run_at.strftime("%Y-%m-%d %H:%M:%S") if (settings and settings.next_run_at and is_active) else "—"

    total_sent = settings.total_sent if settings else 0
    total_err = settings.total_errors if settings else 0
    last_err = f"\n⚠️ <b>Последняя ошибка:</b> {settings.last_error}" if (settings and settings.last_error) else ""

    interval_val = float(settings.interval_minutes) if settings else 30.0
    interval_str = "30 сек." if interval_val == 0.5 else f"{int(interval_val) if interval_val.is_integer() else interval_val} мин."

    text = (
        "🚀 <b>Управление и мониторинг циклической рассылки</b>\n\n"
        f"📊 <b>Текущее состояние:</b> {status_icon} {status_text}\n"
        "────────────────────\n"
        f"📱 <b>Аккаунт:</b> {'✅ Подключен' if user_sess else '❌ Не подключен'}\n"
        f"✉️ <b>Сообщений в пуле:</b> {msg_count} (мин. 2)\n"
        f"📢 <b>Выбрано каналов:</b> {selected_channels}\n"
        f"⏱ <b>Интервал цикла:</b> {interval_str}\n"
        "────────────────────\n"
        f"🕒 <b>Крайний круг:</b> {last_run_str}\n"
        f"⏳ <b>Следующий круг:</b> {next_run_str}\n"
        f"📈 <b>Успешно отправлено:</b> <code>{total_sent}</code> сообщений\n"
        f"❌ <b>Ошибок/пропусков:</b> <code>{total_err}</code>{last_err}\n"
    )

    keyboard = []
    if is_active:
        keyboard.append([
            InlineKeyboardButton(text="⏸ Остановить рассылку", callback_data="bc_stop")
        ])
    else:
        keyboard.append([
            InlineKeyboardButton(text="▶️ Запустить рассылку", callback_data="bc_start")
        ])

    keyboard.append([
        InlineKeyboardButton(text="🔄 Обновить статус", callback_data="menu_control")
    ])
    keyboard.append([
        InlineKeyboardButton(text="⬅️ В главное меню", callback_data="menu_main")
    ])

    return text, InlineKeyboardMarkup(inline_keyboard=keyboard)

@router.callback_query(F.data == "menu_control")
async def cb_control_menu(callback: CallbackQuery, state: FSMContext):
    """
    Отображение экрана управления рассылкой.
    """
    await state.clear()
    user_id = callback.from_user.id
    text, kb = await render_control_view(user_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()

@router.callback_query(F.data == "bc_start")
async def cb_broadcast_start(callback: CallbackQuery):
    """
    Запуск фоновой циклической рассылки.
    """
    user_id = callback.from_user.id
    success, message = await broadcaster.start_broadcast(user_id)

    if not success:
        await callback.answer(message, show_alert=True)
    else:
        await callback.answer("▶️ Рассылка запущена!", show_alert=False)

    text, kb = await render_control_view(user_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass

@router.callback_query(F.data == "bc_stop")
async def cb_broadcast_stop(callback: CallbackQuery):
    """
    Остановка фоновой рассылки.
    """
    user_id = callback.from_user.id
    await broadcaster.stop_broadcast(user_id, reason="Stopped by user")
    await callback.answer("⏸ Рассылка остановлена!", show_alert=False)

    text, kb = await render_control_view(user_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
