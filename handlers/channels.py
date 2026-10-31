import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, update

from bot.database.db import get_session
from bot.database.models import TargetChannel, UserSession
from bot.keyboards.channels_kb import get_channels_pagination_kb
from bot.keyboards.main_kb import get_back_to_main_kb
from bot.services.telegram_client import TelegramClientService

router = Router(name="channels_router")
logger = logging.getLogger(__name__)

async def render_channels_view(user_id: int, page: int = 0):
    """
    Формирует список каналов с пагинацией и клавиатурой.
    """
    async with get_session() as session:
        stmt = select(TargetChannel).where(TargetChannel.user_id == user_id).order_by(TargetChannel.id)
        channels = (await session.execute(stmt)).scalars().all()

    total_count = len(channels)
    selected_count = sum(1 for c in channels if c.is_selected)

    if total_count == 0:
        text = (
            "📢 <b>Список целевых каналов и групп пуст</b>\n\n"
            "Бот пока не обнаружил каналов или групп, в которых у вас есть права на публикацию.\n\n"
            "💡 <i>Нажмите «🔄 Обновить список (MTProto)», чтобы просканировать ваши диалоги в Telegram.</i>"
        )
    else:
        text = (
            "📢 <b>Управление целевыми каналами</b>\n\n"
            "Отметьте каналы и группы, в которые бот будет циклически рассылать сообщения.\n\n"
            f"📊 <b>Выбрано:</b> {selected_count} из {total_count}\n"
            "────────────────────\n"
            "<i>Нажимайте на кнопки с названиями каналов для переключения (✅ / ❌):</i>"
        )

    kb = get_channels_pagination_kb(channels, page=page)
    return text, kb

@router.callback_query(F.data.startswith("menu_channels"))
@router.callback_query(F.data.startswith("chan_page:"))
async def cb_channels_menu(callback: CallbackQuery, state: FSMContext):
    """
    Отображение страницы списка каналов.
    """
    await state.clear()
    user_id = callback.from_user.id

    page = 0
    if ":" in callback.data:
        try:
            page = int(callback.data.split(":")[1])
        except (ValueError, IndexError):
            page = 0

    text, kb = await render_channels_view(user_id, page=page)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("chan_tog:"))
async def cb_channel_toggle(callback: CallbackQuery):
    """
    Переключение выбора конкретного канала (✅ / ❌).
    """
    parts = callback.data.split(":")
    channel_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0
    user_id = callback.from_user.id

    async with get_session() as session:
        stmt = select(TargetChannel).where(TargetChannel.id == channel_id, TargetChannel.user_id == user_id)
        chan = (await session.execute(stmt)).scalar_one_or_none()
        if chan:
            chan.is_selected = not chan.is_selected

    text, kb = await render_channels_view(user_id, page=page)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()

@router.callback_query(F.data.startswith("chan_all:"))
async def cb_channel_select_all(callback: CallbackQuery):
    """
    Выбрать все каналы пользователя.
    """
    page = int(callback.data.split(":")[1]) if ":" in callback.data else 0
    user_id = callback.from_user.id

    async with get_session() as session:
        await session.execute(
            update(TargetChannel)
            .where(TargetChannel.user_id == user_id)
            .values(is_selected=True)
        )

    text, kb = await render_channels_view(user_id, page=page)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer("🔘 Все каналы выбраны!")

@router.callback_query(F.data.startswith("chan_none:"))
async def cb_channel_deselect_all(callback: CallbackQuery):
    """
    Снять выбор со всех каналов.
    """
    page = int(callback.data.split(":")[1]) if ":" in callback.data else 0
    user_id = callback.from_user.id

    async with get_session() as session:
        await session.execute(
            update(TargetChannel)
            .where(TargetChannel.user_id == user_id)
            .values(is_selected=False)
        )

    text, kb = await render_channels_view(user_id, page=page)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer("⚪️ Выбор снят со всех каналов!")

@router.callback_query(F.data == "channels_rescan")
async def cb_channels_rescan(callback: CallbackQuery):
    """
    Повторное сканирование доступных каналов через MTProto.
    """
    user_id = callback.from_user.id

    async with get_session() as session:
        stmt = select(UserSession).where(UserSession.user_id == user_id, UserSession.is_active == True)
        user_sess = (await session.execute(stmt)).scalar_one_or_none()

    if not user_sess:
        await callback.answer("❌ Аккаунт не подключен!", show_alert=True)
        return

    await callback.answer("🔄 Запуск сканирования...", show_alert=False)
    await callback.message.edit_text("⏳ <i>Сканирование диалогов в Telegram через MTProto... Пожалуйста, подождите.</i>", parse_mode="HTML")

    count, msg_res = await TelegramClientService.scan_and_save_dialogs(user_id, user_sess.session_string)

    text, kb = await render_channels_view(user_id, page=0)
    await callback.message.edit_text(
        f"✅ <b>Сканирование завершено!</b>\n{msg_res}\n\n" + text,
        reply_markup=kb,
        parse_mode="HTML"
    )
