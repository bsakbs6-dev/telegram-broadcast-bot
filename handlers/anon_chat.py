import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, update

from bot.database.db import get_session
from bot.database.models import UserSession, AnonChatSettings
from bot.states.anon_states import AnonChatStates
from bot.keyboards.anon_chat_kb import get_anon_chat_kb, get_cancel_anon_kb
from bot.services.anon_chat_worker import anon_chat_service

router = Router(name="anon_chat_router")
logger = logging.getLogger(__name__)

async def render_anon_chat_view(user_id: int):
    """
    Формирует экран мониторинга и настроек анонимного чата @AnonRuBot.
    """
    async with get_session() as session:
        # Сессия пользователя
        sess_stmt = select(UserSession).where(UserSession.user_id == user_id, UserSession.is_active == True)
        user_sess = (await session.execute(sess_stmt)).scalar_one_or_none()

        # Настройки анонимного чата
        set_stmt = select(AnonChatSettings).where(AnonChatSettings.user_id == user_id)
        settings = (await session.execute(set_stmt)).scalar_one_or_none()

    if not settings:
        is_running = False
        message_text = "Привет! Интересует? Напиши +, расскажу подробнее."
        trigger_symbol = "+"
        auto_skip_seconds = 20
        total_dialogs = 0
        total_leads = 0
        last_lead_str = "Еще не было"
    else:
        is_running = anon_chat_service.is_running(user_id) or settings.is_running
        message_text = settings.message_text or "Привет! Интересует? Напиши +"
        trigger_symbol = settings.trigger_symbol or "+"
        auto_skip_seconds = getattr(settings, "auto_skip_seconds", 20) or 20
        total_dialogs = settings.total_dialogs
        total_leads = settings.total_leads
        last_lead_str = settings.last_lead_at.strftime("%Y-%m-%d %H:%M:%S") if settings.last_lead_at else "Еще не было"

    status_icon = "🟢" if is_running else "🔴"
    status_text = "<b>РАБОТАЕТ (в фоне)</b>" if is_running else "<b>ОСТАНОВЛЕН</b>"

    acc_info = f"✅ {user_sess.first_name or 'Пользователь'} ({user_sess.phone_number})" if user_sess else "❌ Не подключен"

    text = (
        "🎭 <b>Авто-рассылка и сбор лидов в @AnonRuBot</b>\n\n"
        f"📊 <b>Состояние:</b> {status_icon} {status_text}\n"
        f"📱 <b>Аккаунт:</b> {acc_info}\n"
        "────────────────────\n"
        f"🎯 <b>Триггер-символ лида:</b> <code>{trigger_symbol}</code>\n"
        f"⏱ <b>Авто-скип (/next):</b> через <code>{auto_skip_seconds} сек.</code>\n"
        f"📈 <b>Найдено собеседников:</b> <code>{total_dialogs}</code>\n"
        f"🔥 <b>Поймано лидов (ответов «{trigger_symbol}»):</b> <code>{total_leads}</code>\n"
        f"🕒 <b>Крайний лид:</b> {last_lead_str}\n"
        "────────────────────\n"
        f"📝 <b>Текст оффера собеседнику:</b>\n"
        f"<i>{message_text}</i>\n"
    )

    kb = get_anon_chat_kb(
        is_running=is_running,
        trigger_symbol=trigger_symbol,
        auto_skip_seconds=auto_skip_seconds,
    )
    return text, kb

@router.callback_query(F.data == "menu_anon_chat")
async def cb_anon_chat_menu(callback: CallbackQuery, state: FSMContext):
    """
    Отображение главного экрана управления анонимным чатом.
    """
    await state.clear()
    user_id = callback.from_user.id
    text, kb = await render_anon_chat_view(user_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()

@router.callback_query(F.data == "anon_start")
async def cb_anon_start(callback: CallbackQuery, bot: Bot):
    """
    Запуск авто-чата в @AnonRuBot.
    """
    user_id = callback.from_user.id
    success, message = await anon_chat_service.start_worker(user_id, bot=bot)

    if not success:
        await callback.answer(message, show_alert=True)
    else:
        await callback.answer("▶️ Авто-чат в @AnonRuBot запущен!", show_alert=False)

    text, kb = await render_anon_chat_view(user_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass

@router.callback_query(F.data == "anon_stop")
async def cb_anon_stop(callback: CallbackQuery):
    """
    Остановка авто-чата.
    """
    user_id = callback.from_user.id
    await anon_chat_service.stop_worker(user_id, reason="Stopped by user")
    await callback.answer("⏸ Авто-чат остановлен!", show_alert=False)

    text, kb = await render_anon_chat_view(user_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass

@router.callback_query(F.data == "anon_edit_text")
async def cb_anon_edit_text(callback: CallbackQuery, state: FSMContext):
    """
    Запрос нового текста для отправки собеседнику.
    """
    await state.set_state(AnonChatStates.waiting_for_message_text)
    prompt = (
        "📝 <b>Редактирование оффера для @AnonRuBot</b>\n\n"
        "Отправьте текст сообщения, который бот будет автоматически отправлять каждому найденному собеседнику:\n\n"
        "<i>Для отмены нажмите кнопку ниже:</i>"
    )
    try:
        await callback.message.edit_text(prompt, reply_markup=get_cancel_anon_kb(), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()

@router.message(AnonChatStates.waiting_for_message_text)
async def process_anon_message_text_input(message: Message, state: FSMContext):
    """
    Сохранение нового текста оффера.
    """
    raw_text = message.text.strip() if message.text else ""
    if not raw_text:
        await message.answer("❌ Текст не может быть пустым. Введите текст сообщения:", reply_markup=get_cancel_anon_kb(), parse_mode="HTML")
        return

    user_id = message.from_user.id

    async with get_session() as session:
        set_stmt = select(AnonChatSettings).where(AnonChatSettings.user_id == user_id)
        settings = (await session.execute(set_stmt)).scalar_one_or_none()
        if settings:
            settings.message_text = raw_text
        else:
            session.add(AnonChatSettings(user_id=user_id, message_text=raw_text))

    await state.clear()
    await message.answer("✅ <b>Текст оффера успешно сохранен!</b>", parse_mode="HTML")
    text, kb = await render_anon_chat_view(user_id)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "anon_edit_trigger")
async def cb_anon_edit_trigger(callback: CallbackQuery, state: FSMContext):
    """
    Запрос изменения триггерного символа / слова.
    """
    await state.set_state(AnonChatStates.waiting_for_trigger_symbol)
    prompt = (
        "🎯 <b>Настройка триггера лида</b>\n\n"
        "Введите символ или слово, при получении которого в диалоге бот будет моментально присылать уведомление администратору (например: <code>+</code>, <code>да</code>, <code>цена</code>):\n\n"
        "<i>Для отмены нажмите кнопку ниже:</i>"
    )
    try:
        await callback.message.edit_text(prompt, reply_markup=get_cancel_anon_kb(), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()

@router.message(AnonChatStates.waiting_for_trigger_symbol)
async def process_anon_trigger_input(message: Message, state: FSMContext):
    """
    Сохранение нового триггера.
    """
    raw_trigger = message.text.strip() if message.text else "+"
    user_id = message.from_user.id

    async with get_session() as session:
        set_stmt = select(AnonChatSettings).where(AnonChatSettings.user_id == user_id)
        settings = (await session.execute(set_stmt)).scalar_one_or_none()
        if settings:
            settings.trigger_symbol = raw_trigger
        else:
            session.add(AnonChatSettings(user_id=user_id, trigger_symbol=raw_trigger))

    await state.clear()
    await message.answer(f"✅ <b>Триггер лида изменен на: <code>{raw_trigger}</code>!</b>", parse_mode="HTML")
    text, kb = await render_anon_chat_view(user_id)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "anon_edit_skip_timeout")
async def cb_anon_edit_skip_timeout(callback: CallbackQuery, state: FSMContext):
    """
    Запрос изменения времени авто-скипа (/next).
    """
    await state.set_state(AnonChatStates.waiting_for_skip_timeout)
    prompt = (
        "⏱ <b>Настройка времени авто-скипа (/next)</b>\n\n"
        "Введите время в секундах (от <code>5</code> до <code>300</code>), через которое бот автоматически отправит <code>/next</code> и перейдет к следующему собеседнику, если текущий собеседник не написал триггер и не пропустил чат сам:\n\n"
        "<i>Например: <code>15</code> или <code>20</code></i>"
    )
    try:
        await callback.message.edit_text(prompt, reply_markup=get_cancel_anon_kb(), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()

@router.message(AnonChatStates.waiting_for_skip_timeout)
async def process_anon_skip_timeout_input(message: Message, state: FSMContext):
    """
    Сохранение нового времени авто-скипа (/next).
    """
    raw_val = (message.text or "").strip()
    if not raw_val.isdigit():
        await message.answer(
            "❌ Введите целое число секунд (например: <code>20</code>):",
            reply_markup=get_cancel_anon_kb(),
            parse_mode="HTML",
        )
        return

    sec = int(raw_val)
    if sec < 5 or sec > 300:
        await message.answer(
            "❌ Значение должно быть в диапазоне от <b>5</b> до <b>300</b> секунд. Попробуйте снова:",
            reply_markup=get_cancel_anon_kb(),
            parse_mode="HTML",
        )
        return

    user_id = message.from_user.id
    async with get_session() as session:
        set_stmt = select(AnonChatSettings).where(AnonChatSettings.user_id == user_id)
        settings = (await session.execute(set_stmt)).scalar_one_or_none()
        if settings:
            settings.auto_skip_seconds = sec
        else:
            session.add(AnonChatSettings(user_id=user_id, auto_skip_seconds=sec))

    await state.clear()
    await message.answer(f"✅ <b>Время авто-скипа (/next) установлено на: <code>{sec} сек.</code>!</b>", parse_mode="HTML")
    text, kb = await render_anon_chat_view(user_id)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "anon_reset_stats")
async def cb_anon_reset_stats(callback: CallbackQuery):
    """
    Сброс счетчиков статистики анонимного чата.
    """
    user_id = callback.from_user.id
    async with get_session() as session:
        await session.execute(
            update(AnonChatSettings)
            .where(AnonChatSettings.user_id == user_id)
            .values(total_dialogs=0, total_leads=0)
        )
    await callback.answer("📊 Статистика сброшена!", show_alert=True)
    text, kb = await render_anon_chat_view(user_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass

@router.callback_query(F.data == "anon_cancel")
async def cb_anon_cancel(callback: CallbackQuery, state: FSMContext):
    """
    Отмена ввода и возврат на главный экран модуля.
    """
    await state.clear()
    user_id = callback.from_user.id
    text, kb = await render_anon_chat_view(user_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()
