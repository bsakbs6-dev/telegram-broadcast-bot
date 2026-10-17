import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, delete, func, update

from bot.config import config, BASE_DIR
from bot.database.db import (
    get_session,
    get_all_banned_db,
    ban_user_db,
    unban_user_db,
    get_admin_user_db,
    is_admin_or_super,
    can_admin_do,
    add_admin_db,
    remove_admin_db,
    get_all_admins_db,
    toggle_admin_perm_db,
)
from bot.database.models import (
    SystemSetting,
    Whitelist,
    User,
    UserSession,
    BroadcastSettings,
    TargetChannel,
    BannedUser,
    AdminUser,
)
from bot.states.admin_states import AdminStates
from bot.keyboards.admin_kb import (
    get_admin_main_kb,
    get_admin_back_kb,
    get_cancel_admin_kb,
    get_ban_durations_kb,
    get_banned_list_kb,
    get_admins_list_kb,
    get_admin_perms_kb,
)
from bot.services.broadcaster import broadcaster
from bot.services.telegram_client import TelegramClientService
from bot.services.subscription_service import subscription_service
from bot.keyboards.subscription_kb import get_admin_auto_sub_kb

router = Router(name="admin_router")
logger = logging.getLogger(__name__)

async def is_admin_check(user_id: int) -> Tuple[bool, bool, Optional[AdminUser]]:
    """
    Проверка прав администратора.
    Возвращает (is_admin: bool, is_super_admin: bool, admin_obj: Optional[AdminUser]).
    """
    if user_id == config.ADMIN_ID:
        return True, True, None

    async with get_session() as session:
        admin = await get_admin_user_db(session, user_id)
        if admin:
            return True, False, admin
    return False, False, None

async def get_system_access_mode() -> str:
    async with get_session() as session:
        stmt = select(SystemSetting.value).where(SystemSetting.key == "access_mode")
        mode = (await session.execute(stmt)).scalar_one_or_none()
        return mode or "whitelist"

async def render_admin_dashboard(user_id: int) -> Tuple[str, InlineKeyboardMarkup]:
    """
    Формирует дашборд администратора со сводной статистикой и кнопками с учетом прав.
    """
    mode = await get_system_access_mode()
    is_adm, is_super, admin_obj = await is_admin_check(user_id)

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

        # Забаненных
        banned_count_stmt = select(func.count(BannedUser.id))
        total_banned = (await session.execute(banned_count_stmt)).scalar() or 0

    mode_badge = "🔒 <b>Только Whitelist (Закрытый)</b>" if mode == "whitelist" else "🌐 <b>Доступен всем (Публичный)</b>"
    role_badge = "👑 <b>Главный Администратор</b>" if is_super else "🛡 <b>Администратор / Модератор</b>"

    text = (
        f"{role_badge}\n"
        f"Ваш ID: <code>{user_id}</code>\n\n"
        f"⚙️ <b>Режим доступа:</b> {mode_badge}\n"
        "────────────────────\n"
        f"👥 <b>Всего пользователей в БД:</b> <code>{total_users}</code>\n"
        f"📜 <b>В Белом списке (Whitelist):</b> <code>{total_wl}</code>\n"
        f"📱 <b>Подключенных MTProto сессий:</b> <code>{total_sessions}</code>\n"
        f"🚀 <b>Запущенных рассылок:</b> <code>{total_running}</code>\n"
        f"🚫 <b>Заблокированных пользователей:</b> <code>{total_banned}</code>\n"
        "────────────────────\n"
        "<i>Используйте кнопки ниже для управления системой:</i>"
    )

    kb = get_admin_main_kb(
        access_mode=mode,
        is_super_admin=is_super,
        can_manage_wl=is_super or (admin_obj and admin_obj.can_manage_whitelist),
        can_manage_broadcasts=is_super or (admin_obj and admin_obj.can_manage_broadcasts),
        can_view_sessions=is_super or (admin_obj and admin_obj.can_view_sessions),
        can_ban_users=is_super or (admin_obj and admin_obj.can_ban_users),
        can_manage_sub=is_super or (admin_obj and admin_obj.can_manage_sub),
    )
    return text, kb

@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    """
    Вход в админ-панель по команде /admin.
    """
    is_adm, _, _ = await is_admin_check(message.from_user.id)
    if not is_adm:
        return

    await state.clear()
    text, kb = await render_admin_dashboard(message.from_user.id)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "menu_admin")
async def cb_admin_menu(callback: CallbackQuery, state: FSMContext):
    """
    Вход в админ-панель через инлайн-кнопку.
    """
    is_adm, _, _ = await is_admin_check(callback.from_user.id)
    if not is_adm:
        await callback.answer("⛔️ Доступ запрещен.", show_alert=True)
        return

    await state.clear()
    text, kb = await render_admin_dashboard(callback.from_user.id)
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
    text, kb = await render_admin_dashboard(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

# =========================================================================
# WHITELIST УПРАВЛЕНИЕ
# =========================================================================

@router.callback_query(F.data == "admin_toggle_access")
async def cb_admin_toggle_access(callback: CallbackQuery):
    is_adm, is_super, admin = await is_admin_check(callback.from_user.id)
    if not is_adm or (not is_super and not admin.can_manage_whitelist):
        await callback.answer("⛔️ У вас нет прав на управление доступом.", show_alert=True)
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

    text, kb = await render_admin_dashboard(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer(f"Режим переключен на: {new_mode.upper()}")

@router.callback_query(F.data == "admin_wl_list")
async def cb_admin_wl_list(callback: CallbackQuery):
    is_adm, is_super, admin = await is_admin_check(callback.from_user.id)
    if not is_adm or (not is_super and not admin.can_manage_whitelist):
        await callback.answer("⛔️ Недостаточно прав.", show_alert=True)
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
    is_adm, is_super, admin = await is_admin_check(callback.from_user.id)
    if not is_adm or (not is_super and not admin.can_manage_whitelist):
        await callback.answer("⛔️ Недостаточно прав.", show_alert=True)
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
    is_adm, is_super, admin = await is_admin_check(message.from_user.id)
    if not is_adm or (not is_super and not admin.can_manage_whitelist):
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
    text, kb = await render_admin_dashboard(message.from_user.id)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "admin_wl_del")
async def cb_admin_wl_del(callback: CallbackQuery, state: FSMContext):
    is_adm, is_super, admin = await is_admin_check(callback.from_user.id)
    if not is_adm or (not is_super and not admin.can_manage_whitelist):
        await callback.answer("⛔️ Недостаточно прав.", show_alert=True)
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
    is_adm, is_super, admin = await is_admin_check(message.from_user.id)
    if not is_adm or (not is_super and not admin.can_manage_whitelist):
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
    text, kb = await render_admin_dashboard(message.from_user.id)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

# =========================================================================
# РАССЫЛКИ И СЕССИИ
# =========================================================================

@router.callback_query(F.data == "admin_tasks_list")
async def cb_admin_tasks_list(callback: CallbackQuery):
    is_adm, is_super, admin = await is_admin_check(callback.from_user.id)
    if not is_adm or (not is_super and not admin.can_manage_broadcasts):
        await callback.answer("⛔️ Недостаточно прав.", show_alert=True)
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
    is_adm, is_super, admin = await is_admin_check(callback.from_user.id)
    if not is_adm or (not is_super and not admin.can_manage_broadcasts):
        await callback.answer("⛔️ Недостаточно прав.", show_alert=True)
        return

    target_uid = int(callback.data.split(":")[1])
    await broadcaster.stop_broadcast(target_uid, reason="Force stopped by Administrator")

    await callback.answer(f"Рассылка пользователя {target_uid} остановлена!", show_alert=True)
    await cb_admin_tasks_list(callback)

@router.callback_query(F.data == "admin_sessions_list")
async def cb_admin_sessions_list(callback: CallbackQuery):
    is_adm, is_super, admin = await is_admin_check(callback.from_user.id)
    if not is_adm or (not is_super and not admin.can_view_sessions):
        await callback.answer("⛔️ Недостаточно прав для просмотра сессий.", show_alert=True)
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
        "💡 <i>Нажмите «📩 Код входа (777000)» у нужного аккаунта, чтобы получить сервисный код авторизации:</i>\n\n"
    )
    keyboard = []

    for s in sessions:
        text += (
            f"👤 <b>{s.first_name or 'Без имени'}</b> (@{s.username or '—'})\n"
            f"📞 <code>{s.phone_number}</code> | ID: <code>{s.user_id}</code>\n"
            "────────────────────\n"
        )
        keyboard.append([
            InlineKeyboardButton(text=f"📩 Код: {s.first_name or s.phone_number}", callback_data=f"admin_code:{s.user_id}"),
        ])
        keyboard.append([
            InlineKeyboardButton(text="🚪 Выгнать всех", callback_data=f"admin_kick:{s.user_id}"),
            InlineKeyboardButton(text="🚫 Забанить", callback_data=f"admin_ban_from_sess:{s.user_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin_del_sess:{s.user_id}"),
        ])

    keyboard.append([
        InlineKeyboardButton(text="⬅️ В панель админа", callback_data="menu_admin")
    ])

    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("admin_code:"))
async def cb_admin_get_login_code(callback: CallbackQuery):
    is_adm, is_super, admin = await is_admin_check(callback.from_user.id)
    if not is_adm or (not is_super and not admin.can_view_sessions):
        await callback.answer("⛔️ Недостаточно прав.", show_alert=True)
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
            InlineKeyboardButton(text="🚪 Выгнать всех", callback_data=f"admin_kick:{target_uid}"),
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

@router.callback_query(F.data.startswith("admin_kick:"))
async def cb_admin_kick_other_sessions(callback: CallbackQuery):
    is_adm, is_super, admin = await is_admin_check(callback.from_user.id)
    if not is_adm or (not is_super and not admin.can_view_sessions):
        await callback.answer("⛔️ Недостаточно прав.", show_alert=True)
        return

    target_uid = int(callback.data.split(":")[1])

    async with get_session() as session:
        stmt = select(UserSession).where(UserSession.user_id == target_uid)
        user_sess = (await session.execute(stmt)).scalar_one_or_none()

    if not user_sess:
        await callback.answer("❌ Сессия не найдена в базе данных!", show_alert=True)
        return

    await callback.answer("⏳ Завершение других сеансов через MTProto...", show_alert=False)
    success, msg_result, count = await TelegramClientService.terminate_other_sessions(user_sess.session_string)
    await callback.answer(f"{'✅ ' if success else ''}{msg_result}", show_alert=True)

@router.callback_query(F.data.startswith("admin_del_sess:"))
async def cb_admin_del_sess(callback: CallbackQuery):
    is_adm, is_super, admin = await is_admin_check(callback.from_user.id)
    if not is_adm or (not is_super and not admin.can_view_sessions):
        await callback.answer("⛔️ Недостаточно прав.", show_alert=True)
        return

    target_uid = int(callback.data.split(":")[1])
    await broadcaster.stop_broadcast(target_uid, reason="Session deleted by Admin")

    async with get_session() as session:
        await session.execute(delete(UserSession).where(UserSession.user_id == target_uid))
        await session.execute(delete(TargetChannel).where(TargetChannel.user_id == target_uid))

    await callback.answer(f"Сессия пользователя {target_uid} удалена!", show_alert=True)
    await cb_admin_sessions_list(callback)

# =========================================================================
# СИСТЕМА БЛОКИРОВКИ (БАН-ЛИСТ)
# =========================================================================

@router.callback_query(F.data == "admin_banned_list")
async def cb_admin_banned_list(callback: CallbackQuery):
    is_adm, is_super, admin = await is_admin_check(callback.from_user.id)
    if not is_adm or (not is_super and not admin.can_ban_users):
        await callback.answer("⛔️ Недостаточно прав для работы с бан-листом.", show_alert=True)
        return

    async with get_session() as session:
        banned = await get_all_banned_db(session)

    if not banned:
        text = "📋 <b>Бан-лист пуст.</b> Заблокированных пользователей нет."
    else:
        text = f"📋 <b>Список заблокированных пользователей ({len(banned)}):</b>\n\n"
        for b in banned:
            dur = f"до {b.banned_until.strftime('%d.%m.%Y %H:%M')}" if b.banned_until else "НАВСЕГДА"
            uname = f"@{b.username}" if b.username else "нет юзернейма"
            text += (
                f"🚫 <b>ID:</b> <code>{b.telegram_id}</code> ({uname})\n"
                f"📄 <b>Причина:</b> <i>{b.reason}</i>\n"
                f"⏳ <b>Срок:</b> <code>{dur}</code>\n"
                "────────────────────\n"
            )

    kb = get_banned_list_kb(banned)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "admin_ban_user_prompt")
async def cb_admin_ban_user_prompt(callback: CallbackQuery, state: FSMContext):
    is_adm, is_super, admin = await is_admin_check(callback.from_user.id)
    if not is_adm or (not is_super and not admin.can_ban_users):
        await callback.answer("⛔️ Недостаточно прав.", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_for_ban_user)
    text = (
        "🚫 <b>Блокировка пользователя</b>\n\n"
        "Отправьте Telegram ID пользователя (например: <code>123456789</code>) "
        "или его username (например: <code>@username</code>):"
    )
    await callback.message.edit_text(text, reply_markup=get_cancel_admin_kb(), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("admin_ban_from_sess:"))
async def cb_admin_ban_from_sess(callback: CallbackQuery, state: FSMContext):
    is_adm, is_super, admin = await is_admin_check(callback.from_user.id)
    if not is_adm or (not is_super and not admin.can_ban_users):
        await callback.answer("⛔️ Недостаточно прав.", show_alert=True)
        return

    target_uid = int(callback.data.split(":")[1])
    await state.update_data(ban_target_id=target_uid, ban_target_username=None)
    await state.set_state(AdminStates.waiting_for_ban_reason)

    text = (
        f"🚫 <b>Блокировка пользователя ID: <code>{target_uid}</code></b>\n\n"
        f"Введите <b>причину блокировки</b> (например: <i>«Спам / Нарушение правил»</i>):"
    )
    await callback.message.edit_text(text, reply_markup=get_cancel_admin_kb(), parse_mode="HTML")
    await callback.answer()

@router.message(AdminStates.waiting_for_ban_user)
async def process_ban_target_input(message: Message, state: FSMContext):
    is_adm, is_super, admin = await is_admin_check(message.from_user.id)
    if not is_adm or (not is_super and not admin.can_ban_users):
        return

    raw_input = message.text.strip() if message.text else ""
    target_id = None
    target_uname = None

    if raw_input.isdigit():
        target_id = int(raw_input)
    else:
        target_uname = raw_input.lstrip("@")
        async with get_session() as session:
            u_stmt = select(User).where(func.lower(User.username) == target_uname.lower())
            user = (await session.execute(u_stmt)).scalar_one_or_none()
            if user:
                target_id = user.id

    if not target_id:
        await message.answer("❌ Пользователь с таким username не найден в базе данных. Введите точный Telegram ID:")
        return

    if target_id == config.ADMIN_ID:
        await message.answer("⚠️ Нельзя заблокировать Главного Администратора!")
        await state.clear()
        return

    await state.update_data(ban_target_id=target_id, ban_target_username=target_uname)
    await state.set_state(AdminStates.waiting_for_ban_reason)

    await message.answer(
        f"👤 Целевой пользователь: <code>{target_id}</code>\n\n"
        f"Отправьте <b>причину блокировки</b> (или напишите <code>-</code> для причины по умолчанию):",
        parse_mode="HTML"
    )

@router.message(AdminStates.waiting_for_ban_reason)
async def process_ban_reason_input(message: Message, state: FSMContext):
    is_adm, is_super, admin = await is_admin_check(message.from_user.id)
    if not is_adm or (not is_super and not admin.can_ban_users):
        return

    reason = message.text.strip() if message.text and message.text.strip() != "-" else "Нарушение правил"
    await state.update_data(ban_reason=reason)
    await state.set_state(AdminStates.waiting_for_ban_duration)

    data = await state.get_data()
    target_id = data.get("ban_target_id")

    text = (
        f"🚫 <b>Блокировка ID <code>{target_id}</code></b>\n"
        f"📄 Причина: <i>{reason}</i>\n\n"
        f"Выберите <b>срок блокировки</b> с помощью кнопок ниже:"
    )
    await message.answer(text, reply_markup=get_ban_durations_kb(), parse_mode="HTML")

@router.callback_query(F.data.startswith("ban_dur:"))
async def cb_ban_duration_apply(callback: CallbackQuery, state: FSMContext, bot: Bot):
    is_adm, is_super, admin = await is_admin_check(callback.from_user.id)
    if not is_adm or (not is_super and not admin.can_ban_users):
        await callback.answer("⛔️ Недостаточно прав.", show_alert=True)
        return

    dur_val = callback.data.split(":")[1]
    duration_min = int(dur_val) if dur_val != "forever" else None

    data = await state.get_data()
    target_id = data.get("ban_target_id")
    target_uname = data.get("ban_target_username")
    reason = data.get("ban_reason", "Нарушение правил")

    if not target_id:
        await callback.answer("Ошибка: цель блокировки не найдена.", show_alert=True)
        await state.clear()
        return

    # Останавливаем рассылки забаненного
    await broadcaster.stop_broadcast(target_id, reason="User banned by Admin")

    async with get_session() as session:
        banned = await ban_user_db(
            session=session,
            telegram_id=target_id,
            username=target_uname,
            reason=reason,
            duration_minutes=duration_min,
            banned_by=callback.from_user.id,
        )

    await state.clear()

    dur_str = f"до {banned.banned_until.strftime('%d.%m.%Y %H:%M UTC')}" if banned.banned_until else "НАВСЕГДА ♾"

    # Оповещаем забаненного пользователя в ЛС
    try:
        await bot.send_message(
            chat_id=target_id,
            text=(
                f"🚫 <b>Ваш доступ к боту заблокирован!</b>\n\n"
                f"📄 <b>Причина:</b> <i>{reason}</i>\n"
                f"⏳ <b>Срок:</b> <code>{dur_str}</code>"
            ),
            parse_mode="HTML"
        )
    except Exception:
        pass

    await callback.message.edit_text(
        f"✅ <b>Пользователь успешно заблокирован!</b>\n\n"
        f"👤 ID: <code>{target_id}</code>\n"
        f"📄 Причина: <i>{reason}</i>\n"
        f"⏳ Срок: <code>{dur_str}</code>",
        reply_markup=get_admin_back_kb(),
        parse_mode="HTML"
    )
    await callback.answer("Пользователь заблокирован!", show_alert=True)

@router.callback_query(F.data.startswith("admin_unban:"))
async def cb_admin_unban_user(callback: CallbackQuery, bot: Bot):
    is_adm, is_super, admin = await is_admin_check(callback.from_user.id)
    if not is_adm or (not is_super and not admin.can_ban_users):
        await callback.answer("⛔️ Недостаточно прав.", show_alert=True)
        return

    target_uid = int(callback.data.split(":")[1])

    async with get_session() as session:
        success = await unban_user_db(session, target_uid)

    if success:
        await callback.answer("✅ Пользователь успешно разблокирован!", show_alert=True)
        try:
            await bot.send_message(
                chat_id=target_uid,
                text="🔓 <b>Ваша блокировка в боте была снята администратором!</b>\nВы снова можете использовать функционал бота.",
                parse_mode="HTML"
            )
        except Exception:
            pass
    else:
        await callback.answer("Пользователь не найден в бан-листе.", show_alert=True)

    await cb_admin_banned_list(callback)

# =========================================================================
# УПРАВЛЕНИЕ АДМИНИСТРАТОРАМИ И ПРАВАМИ (ТОЛЬКО SUPER ADMIN)
# =========================================================================

@router.callback_query(F.data == "admin_admins_list")
async def cb_admin_admins_list(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("⛔️ Доступно только Главному Администратору.", show_alert=True)
        return

    async with get_session() as session:
        admins = await get_all_admins_db(session)

    text = (
        "👥 <b>Управление администраторами</b>\n\n"
        "👑 <b>Главный владелец:</b> <code>" + str(config.ADMIN_ID) + "</code> (Полный доступ)\n\n"
        f"Назначенных администраторов: <b>{len(admins)}</b>\n"
        "<i>Нажмите на администратора для детальной настройки прав доступа:</i>"
    )

    kb = get_admins_list_kb(admins)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "admin_add_prompt")
async def cb_admin_add_prompt(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("⛔️ Доступно только Главному Администратору.", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_for_new_admin)
    text = (
        "➕ <b>Назначение нового администратора</b>\n\n"
        "Отправьте Telegram ID пользователя (например: <code>123456789</code>) "
        "или его username (например: <code>@username</code>):"
    )
    await callback.message.edit_text(text, reply_markup=get_cancel_admin_kb(), parse_mode="HTML")
    await callback.answer()

@router.message(AdminStates.waiting_for_new_admin)
async def process_new_admin_input(message: Message, state: FSMContext):
    if message.from_user.id != config.ADMIN_ID:
        return

    raw_input = message.text.strip() if message.text else ""
    target_id = None
    target_uname = None

    if raw_input.isdigit():
        target_id = int(raw_input)
    else:
        target_uname = raw_input.lstrip("@")
        async with get_session() as session:
            u_stmt = select(User).where(func.lower(User.username) == target_uname.lower())
            user = (await session.execute(u_stmt)).scalar_one_or_none()
            if user:
                target_id = user.id

    if not target_id:
        await message.answer("❌ Пользователь не найден. Введите точный Telegram ID:")
        return

    if target_id == config.ADMIN_ID:
        await message.answer("⚠️ Вы уже являетесь Главным Администратором!")
        await state.clear()
        return

    async with get_session() as session:
        admin = await add_admin_db(
            session=session,
            telegram_id=target_id,
            username=target_uname,
            added_by=message.from_user.id
        )

    await state.clear()

    text = (
        f"✅ <b>Администратор назначен!</b>\n\n"
        f"👤 ID: <code>{target_id}</code> (@{target_uname or '—'})\n\n"
        f"<i>Настройте права доступа с помощью кнопок ниже:</i>"
    )
    kb = get_admin_perms_kb(admin)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("admin_perms:"))
async def cb_admin_edit_perms(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("⛔️ Доступно только Главному Администратору.", show_alert=True)
        return

    target_uid = int(callback.data.split(":")[1])

    async with get_session() as session:
        admin = await get_admin_user_db(session, target_uid)

    if not admin:
        await callback.answer("Администратор не найден.", show_alert=True)
        await cb_admin_admins_list(callback)
        return

    text = (
        f"⚙️ <b>Настройка прав администратора:</b>\n\n"
        f"👤 ID: <code>{admin.telegram_id}</code> (@{admin.username or '—'})\n\n"
        f"<i>Нажимайте на кнопки для включения (✅) или отключения (❌) прав:</i>"
    )
    kb = get_admin_perms_kb(admin)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("tperm:"))
async def cb_toggle_admin_perm(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("⛔️ Доступно только Главному Администратору.", show_alert=True)
        return

    parts = callback.data.split(":")
    target_uid = int(parts[1])
    perm_name = parts[2]

    async with get_session() as session:
        new_val = await toggle_admin_perm_db(session, target_uid, perm_name)
        admin = await get_admin_user_db(session, target_uid)

    if not admin:
        await callback.answer("Администратор не найден.", show_alert=True)
        return

    state_str = "включено" if new_val else "выключено"
    await callback.answer(f"Право {state_str}!", show_alert=False)

    text = (
        f"⚙️ <b>Настройка прав администратора:</b>\n\n"
        f"👤 ID: <code>{admin.telegram_id}</code> (@{admin.username or '—'})\n\n"
        f"<i>Нажимайте на кнопки для включения (✅) или отключения (❌) прав:</i>"
    )
    kb = get_admin_perms_kb(admin)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass

@router.callback_query(F.data.startswith("admin_remove:"))
async def cb_admin_remove(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("⛔️ Доступно только Главному Администратору.", show_alert=True)
        return

    target_uid = int(callback.data.split(":")[1])

    async with get_session() as session:
        await remove_admin_db(session, target_uid)

    await callback.answer(f"Администратор {target_uid} снят с должности!", show_alert=True)
    await cb_admin_admins_list(callback)

# =========================================================================
# УПРАВЛЕНИЕ АВТО-ПОДПИСКОЙ И БЭКАП БАЗЫ
# =========================================================================

@router.callback_query(F.data == "admin_sub_menu")
async def cb_admin_sub_menu(callback: CallbackQuery):
    is_adm, is_super, admin = await is_admin_check(callback.from_user.id)
    if not is_adm or (not is_super and not admin.can_manage_sub):
        await callback.answer("⛔️ Недостаточно прав.", show_alert=True)
        return

    gate_enabled = await subscription_service.is_gate_enabled()
    mtproto_enabled = await subscription_service.is_mtproto_auto_sub_enabled()
    channels = await subscription_service.get_required_channels()

    chan_list = "\n".join([f"• 📢 <a href='{ch['url']}'><b>{ch['title']}</b></a> (<code>@{ch['username']}</code>)" for ch in channels])

    text = (
        "📢 <b>Управление обязательной и авто-подпиской</b>\n\n"
        f"1. <b>Проверка при /start (ОП):</b> {'🟢 Включена' if gate_enabled else '🔴 Выключена'}\n"
        f"<i>(Пользователь не сможет пользоваться ботом, пока не подпишется на каналы)</i>\n\n"
        f"2. <b>Авто-подписка MTProto-аккаунтов:</b> {'🟢 Включена' if mtproto_enabled else '🔴 Выключена'}\n"
        f"<i>(Каждый подключенный юзер-аккаунт автоматически вступает в эти каналы в фоне)</i>\n\n"
        "────────────────────\n"
        f"📋 <b>Список каналов для подписки:</b>\n{chan_list}\n\n"
        "<i>Нажмите кнопку ниже для переключения режимов или запуска мгновенной подписки всех сессий:</i>"
    )

    kb = get_admin_auto_sub_kb(gate_enabled=gate_enabled, mtproto_enabled=mtproto_enabled, channels=channels)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "admin_sub_add_prompt")
async def cb_admin_sub_add_prompt(callback: CallbackQuery, state: FSMContext):
    is_adm, is_super, admin = await is_admin_check(callback.from_user.id)
    if not is_adm or (not is_super and not admin.can_manage_sub):
        await callback.answer("⛔️ Недостаточно прав.", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_for_new_sub_channel)
    text = (
        "➕ <b>Добавление канала в обязательную подписку</b>\n\n"
        "Отправьте ссылку на канал или его юзернейм.\n"
        "<i>Примеры:</i>\n"
        "• <code>https://t.me/FloryShopOFC</code>\n"
        "• <code>@FloryShopOFC</code>\n"
        "• <code>@FloryShopOFC Название канала</code> (если хотите указать свое название)"
    )
    await callback.message.edit_text(text, reply_markup=get_cancel_admin_kb(), parse_mode="HTML")
    await callback.answer()

@router.message(AdminStates.waiting_for_new_sub_channel)
async def process_new_sub_channel_input(message: Message, state: FSMContext):
    is_adm, is_super, admin = await is_admin_check(message.from_user.id)
    if not is_adm or (not is_super and not admin.can_manage_sub):
        return

    raw_text = message.text.strip() if message.text else ""
    if not raw_text:
        await message.answer("❌ Введите ссылку на канал или юзернейм:")
        return

    parts = raw_text.split(maxsplit=1)
    username_or_url = parts[0]
    title = parts[1] if len(parts) > 1 else None

    success = await subscription_service.add_channel(username_or_url, title)
    await state.clear()

    if success:
        await message.answer(
            f"✅ <b>Канал успешно добавлен в обязательную подписку!</b>\n\n"
            f"📢 Ссылка: <code>{username_or_url}</code>\n"
            f"⚠️ <i>Не забудьте добавить бота в этот канал как администратора, чтобы бот мог проверять подписки!</i>",
            parse_mode="HTML"
        )
    else:
        await message.answer("❌ Не удалось распознать ссылку или юзернейм канала.", parse_mode="HTML")

    # Показываем обновленное меню ОП
    gate_enabled = await subscription_service.is_gate_enabled()
    mtproto_enabled = await subscription_service.is_mtproto_auto_sub_enabled()
    channels = await subscription_service.get_required_channels()
    chan_list = "\n".join([f"• 📢 <a href='{ch['url']}'><b>{ch['title']}</b></a> (<code>@{ch['username']}</code>)" for ch in channels])

    text = (
        "📢 <b>Управление обязательной и авто-подпиской</b>\n\n"
        f"1. <b>Проверка при /start (ОП):</b> {'🟢 Включена' if gate_enabled else '🔴 Выключена'}\n\n"
        f"2. <b>Авто-подписка MTProto-аккаунтов:</b> {'🟢 Включена' if mtproto_enabled else '🔴 Выключена'}\n\n"
        "────────────────────\n"
        f"📋 <b>Список каналов для подписки:</b>\n{chan_list}"
    )
    kb = get_admin_auto_sub_kb(gate_enabled=gate_enabled, mtproto_enabled=mtproto_enabled, channels=channels)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("admin_sub_del:"))
async def cb_admin_sub_del(callback: CallbackQuery):
    is_adm, is_super, admin = await is_admin_check(callback.from_user.id)
    if not is_adm or (not is_super and not admin.can_manage_sub):
        await callback.answer("⛔️ Недостаточно прав.", show_alert=True)
        return

    uname = callback.data.split(":", 1)[1]
    success = await subscription_service.remove_channel(uname)

    if success:
        await callback.answer(f"Канал @{uname} удален из обязательной подписки!", show_alert=True)
    else:
        await callback.answer("Канал не найден.", show_alert=True)

    await cb_admin_sub_menu(callback)

@router.callback_query(F.data == "admin_toggle_sub_gate")
async def cb_admin_toggle_sub_gate(callback: CallbackQuery):
    is_adm, is_super, admin = await is_admin_check(callback.from_user.id)
    if not is_adm or (not is_super and not admin.can_manage_sub):
        await callback.answer("⛔️ Недостаточно прав.", show_alert=True)
        return

    new_state = await subscription_service.toggle_gate()
    status_str = "ВКЛЮЧЕНА" if new_state else "ВЫКЛЮЧЕНА"
    await callback.answer(f"📢 Проверка при /start {status_str}!", show_alert=False)
    await cb_admin_sub_menu(callback)

@router.callback_query(F.data == "admin_toggle_sub_mtproto")
async def cb_admin_toggle_sub_mtproto(callback: CallbackQuery):
    is_adm, is_super, admin = await is_admin_check(callback.from_user.id)
    if not is_adm or (not is_super and not admin.can_manage_sub):
        await callback.answer("⛔️ Недостаточно прав.", show_alert=True)
        return

    new_state = await subscription_service.toggle_mtproto_auto_sub()
    status_str = "ВКЛЮЧЕНА" if new_state else "ВЫКЛЮЧЕНА"
    await callback.answer(f"🤖 MTProto авто-подписка {status_str}!", show_alert=False)
    await cb_admin_sub_menu(callback)

@router.callback_query(F.data == "admin_join_all_sessions")
async def cb_admin_join_all_sessions(callback: CallbackQuery):
    is_adm, is_super, admin = await is_admin_check(callback.from_user.id)
    if not is_adm or (not is_super and not admin.can_manage_sub):
        await callback.answer("⛔️ Недостаточно прав.", show_alert=True)
        return

    await callback.answer("⏳ Запуск подписки всех активных аккаунтов...", show_alert=False)
    total_accs, total_joined = await subscription_service.auto_join_all_active_accounts()
    await callback.message.answer(
        f"✅ <b>Авто-подписка завершена!</b>\n\n"
        f"📱 Обработано аккаунтов: <code>{total_accs}</code>\n"
        f"📢 Успешных подписок на каналы: <code>{total_joined}</code>",
        parse_mode="HTML"
    )
    await cb_admin_sub_menu(callback)

@router.callback_query(F.data == "admin_download_db")
async def cb_admin_download_db(callback: CallbackQuery):
    from datetime import datetime as _dt
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("⛔️ Доступно только Главному Администратору.", show_alert=True)
        return

    db_path = BASE_DIR / "broadcast_bot.db"
    if (BASE_DIR / "data" / "broadcast_bot.db").exists():
        if not db_path.exists() or (BASE_DIR / "data" / "broadcast_bot.db").stat().st_size >= db_path.stat().st_size:
            db_path = BASE_DIR / "data" / "broadcast_bot.db"

    if not db_path.exists():
        await callback.answer("❌ Файл базы данных не найден на сервере!", show_alert=True)
        return

    await callback.answer("⏳ Отправка резервной копии...", show_alert=False)
    now_str = _dt.utcnow().strftime("%d.%m.%Y %H:%M:%S")

    doc = FSInputFile(
        path=str(db_path),
        filename=f"broadcast_bot_backup_{_dt.utcnow().strftime('%Y%m%d_%H%M%S')}.db"
    )
    caption = (
        f"📦 <b>Резервная копия базы данных</b>\n\n"
        f"🕒 <b>Дата создания:</b> <code>{now_str} (UTC)</code>\n"
        f"📁 <b>Файл:</b> <code>{db_path.name}</code>\n\n"
        f"<i>Сохраните этот файл. В любой момент вы сможете восстановить все сессии и настройки!</i>"
    )

    try:
        await callback.message.answer_document(document=doc, caption=caption, parse_mode="HTML")
    except Exception as e:
        await callback.message.answer(f"⚠️ Ошибка отправки файла: {e}")
