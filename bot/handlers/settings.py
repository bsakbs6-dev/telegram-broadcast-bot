import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select

from bot.database.db import get_session
from bot.database.models import BroadcastSettings
from bot.states.settings_states import SettingsStates
from bot.keyboards.settings_kb import get_settings_kb
from bot.keyboards.main_kb import get_back_to_main_kb

router = Router(name="settings_router")
logger = logging.getLogger(__name__)

async def render_settings_view(user_id: int):
    """
    Формирует текст и клавиатуру настроек рассылки.
    """
    async with get_session() as session:
        stmt = select(BroadcastSettings).where(BroadcastSettings.user_id == user_id)
        settings = (await session.execute(stmt)).scalar_one_or_none()

    if not settings:
        interval = 30.0
        rotation = "random"
        j_min = 3
        j_max = 10
    else:
        interval = float(settings.interval_minutes)
        rotation = settings.rotation_mode
        j_min = settings.random_jitter_min
        j_max = settings.random_jitter_max

    rot_descr = "Случайный выбор текста из пула (Random)" if rotation == "random" else "Последовательный перебор (Round-Robin)"
    interval_label = "30 сек." if interval == 0.5 else f"{int(interval) if interval.is_integer() else interval} мин."

    text = (
        "⚙️ <b>Параметры и тайминги циклической рассылки</b>\n\n"
        f"⏱ <b>Интервал между кругами:</b> {interval_label}\n"
        f"🛡 <b>Безопасный Jitter (пауза между чатами):</b> {j_min}–{j_max} сек.\n"
        f"🔄 <b>Режим ротации сообщений:</b> {rot_descr}\n\n"
        "<i>Выберите готовый пресет интервала или введите собственное значение:</i>"
    )

    kb = get_settings_kb(
        interval_minutes=interval,
        rotation_mode=rotation,
        jitter_min=j_min,
        jitter_max=j_max,
    )
    return text, kb

@router.callback_query(F.data == "menu_settings")
async def cb_settings_menu(callback: CallbackQuery, state: FSMContext):
    """
    Отображение меню настроек.
    """
    await state.clear()
    user_id = callback.from_user.id
    text, kb = await render_settings_view(user_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()

@router.callback_query(F.data.startswith("set_interval:"))
async def cb_set_preset_interval(callback: CallbackQuery):
    """
    Установка предустановленного интервала.
    """
    interval = float(callback.data.split(":")[1])
    user_id = callback.from_user.id

    async with get_session() as session:
        stmt = select(BroadcastSettings).where(BroadcastSettings.user_id == user_id)
        settings = (await session.execute(stmt)).scalar_one_or_none()
        if settings:
            settings.interval_minutes = interval

    text, kb = await render_settings_view(user_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    interval_label = "30 сек." if interval == 0.5 else f"{int(interval) if interval.is_integer() else interval} мин."
    await callback.answer(f"⏱ Интервал установлен: {interval_label}")

@router.callback_query(F.data == "set_toggle_rotation")
async def cb_toggle_rotation(callback: CallbackQuery):
    """
    Переключение режима ротации текстов (random <-> round_robin).
    """
    user_id = callback.from_user.id

    async with get_session() as session:
        stmt = select(BroadcastSettings).where(BroadcastSettings.user_id == user_id)
        settings = (await session.execute(stmt)).scalar_one_or_none()
        if settings:
            settings.rotation_mode = "round_robin" if settings.rotation_mode == "random" else "random"

    text, kb = await render_settings_view(user_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer("🔄 Режим ротации переключен!")

@router.callback_query(F.data == "set_interval_custom")
async def cb_set_custom_interval(callback: CallbackQuery, state: FSMContext):
    """
    Запрос пользовательского интервала.
    """
    await state.set_state(SettingsStates.waiting_for_custom_interval)
    text = (
        "✍️ <b>Ввод пользовательского интервала</b>\n\n"
        "Отправьте интервал между полными кругами рассылки (минимально: <b>30 сек</b>):\n\n"
        "👉 Примеры:\n"
        "• <code>30 сек</code> или <code>0.5</code> — 30 секунд\n"
        "• <code>1</code> — 1 минута\n"
        "• <code>15</code> — 15 минут"
    )
    try:
        await callback.message.edit_text(text, reply_markup=get_back_to_main_kb(), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()

@router.message(SettingsStates.waiting_for_custom_interval)
async def process_custom_interval_input(message: Message, state: FSMContext):
    """
    Валидация и сохранение кастомного интервала от 30 секунд.
    """
    import re
    raw_val = message.text.strip().lower() if message.text else ""

    sec_match = re.match(r"^(\d+)\s*(?:сек|с|s|sec)$", raw_val)
    if sec_match:
        seconds = int(sec_match.group(1))
        if seconds < 30:
            await message.answer("❌ Минимальный интервал — <b>30 секунд</b>. Попробуйте еще раз:", parse_mode="HTML")
            return
        interval = round(seconds / 60.0, 2)
    else:
        try:
            interval = float(raw_val.replace(",", "."))
            if interval < 0.5 or interval > 1440:
                await message.answer("❌ Интервал должен быть в пределах от <b>30 секунд</b> (0.5 мин) до <b>1440 минут</b>. Попробуйте еще раз:", parse_mode="HTML")
                return
        except ValueError:
            await message.answer("❌ Неверный формат. Введите число минут или секунд (например: <code>30 сек</code> или <code>1</code>):", parse_mode="HTML")
            return

    user_id = message.from_user.id

    async with get_session() as session:
        stmt = select(BroadcastSettings).where(BroadcastSettings.user_id == user_id)
        settings = (await session.execute(stmt)).scalar_one_or_none()
        if settings:
            settings.interval_minutes = interval

    await state.clear()
    interval_label = "30 сек." if interval == 0.5 else f"{int(interval) if interval.is_integer() else interval} мин."
    await message.answer(f"✅ <b>Интервал {interval_label} успешно сохранен!</b>", parse_mode="HTML")

    text, kb = await render_settings_view(user_id)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")
