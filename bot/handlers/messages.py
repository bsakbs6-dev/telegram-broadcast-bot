import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, delete

from bot.database.db import get_session
from bot.database.models import BroadcastMessage
from bot.states.message_states import MessageStates
from bot.keyboards.messages_kb import get_messages_pool_kb, get_cancel_message_kb
from bot.keyboards.main_kb import get_back_to_main_kb

router = Router(name="messages_router")
logger = logging.getLogger(__name__)

async def render_messages_view(user_id: int):
    """
    Формирует текст и клавиатуру пула сообщений.
    """
    async with get_session() as session:
        stmt = select(BroadcastMessage).where(BroadcastMessage.user_id == user_id).order_by(BroadcastMessage.slot_number)
        messages = (await session.execute(stmt)).scalars().all()
        messages_by_slot = {m.slot_number: m for m in messages}

    count = len(messages_by_slot)
    status_warning = ""
    if count < 2:
        status_warning = "\n⚠️ <i>Необходимо минимум 2 текста для работы ротации и запуска рассылки.</i>"

    text = (
        "✉️ <b>Пул сообщений для циклической ротации</b>\n\n"
        "Бот автоматически чередует или случайно выбирает текст из пула при отправке в каждый канал, "
        "что значительно снижает вероятность попадания аккаунта под спам-фильтры Telegram.\n\n"
        f"📊 <b>Заполнено слотов:</b> {count} из 4 (от 2 до 4){status_warning}\n\n"
    )

    for slot in range(1, 5):
        msg = messages_by_slot.get(slot)
        if msg:
            text += f"🔹 <b>Слот #{slot}:</b>\n{msg.text}\n────────────────────\n"
        else:
            text += f"▫️ <b>Слот #{slot}:</b> <i>[Не заполнено]</i>\n────────────────────\n"

    kb = get_messages_pool_kb(messages_by_slot)
    return text, kb

@router.callback_query(F.data == "menu_messages")
async def cb_messages_menu(callback: CallbackQuery, state: FSMContext):
    """
    Отображение меню пула сообщений.
    """
    await state.clear()
    user_id = callback.from_user.id
    text, kb = await render_messages_view(user_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        pass
    await callback.answer()

@router.callback_query(F.data.startswith("msg_add:") | F.data.startswith("msg_edit:"))
async def cb_msg_edit_or_add(callback: CallbackQuery, state: FSMContext):
    """
    Запрос ввода текста для выбранного слота.
    """
    slot_str = callback.data.split(":")[1]
    slot_num = int(slot_str)

    await state.update_data(target_slot=slot_num)
    await state.set_state(MessageStates.waiting_for_message_text)

    prompt = (
        f"📝 <b>Редактирование текста для Слота #{slot_num}</b>\n\n"
        "Отправьте текст сообщения. Вы можете использовать форматирование Telegram "
        "(жирный, курсив, скрытый текст, ссылки, эмодзи).\n\n"
        "<i>Для отмены нажмите кнопку ниже:</i>"
    )
    try:
        await callback.message.edit_text(prompt, reply_markup=get_cancel_message_kb(), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()

@router.callback_query(F.data == "msg_cancel")
async def cb_msg_cancel(callback: CallbackQuery, state: FSMContext):
    """
    Отмена добавления/редактирования текста.
    """
    await state.clear()
    user_id = callback.from_user.id
    text, kb = await render_messages_view(user_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        pass
    await callback.answer()

@router.callback_query(F.data.startswith("msg_del:"))
async def cb_msg_delete(callback: CallbackQuery):
    """
    Удаление сообщения из слота.
    """
    slot_str = callback.data.split(":")[1]
    slot_num = int(slot_str)
    user_id = callback.from_user.id

    async with get_session() as session:
        await session.execute(
            delete(BroadcastMessage)
            .where(BroadcastMessage.user_id == user_id, BroadcastMessage.slot_number == slot_num)
        )

    text, kb = await render_messages_view(user_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        pass
    await callback.answer(f"🗑 Слот #{slot_num} очищен")

@router.message(MessageStates.waiting_for_message_text)
async def process_message_text_input(message: Message, state: FSMContext):
    """
    Сохранение введенного пользователем текста с форматированием.
    """
    data = await state.get_data()
    slot_num = data.get("target_slot", 1)
    user_id = message.from_user.id

    # Получаем текст с сохранением HTML-разметки aiogram
    formatted_text = message.html_text or message.text or ""

    if not formatted_text.strip():
        await message.answer(
            "❌ Сообщение не может быть пустым. Отправьте текст:",
            reply_markup=get_cancel_message_kb(),
            parse_mode="HTML"
        )
        return

    async with get_session() as session:
        stmt = select(BroadcastMessage).where(
            BroadcastMessage.user_id == user_id,
            BroadcastMessage.slot_number == slot_num
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()

        if existing:
            existing.text = formatted_text
        else:
            session.add(BroadcastMessage(
                user_id=user_id,
                slot_number=slot_num,
                text=formatted_text
            ))

    await state.clear()
    await message.answer(f"✅ <b>Текст для Слота #{slot_num} успешно сохранен!</b>", parse_mode="HTML")

    text, kb = await render_messages_view(user_id)
    await message.answer(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
