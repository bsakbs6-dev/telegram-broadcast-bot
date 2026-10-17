from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import List, Optional
from bot.database.models import BannedUser, AdminUser

def get_admin_main_kb(
    access_mode: str,
    is_super_admin: bool = True,
    can_manage_wl: bool = True,
    can_manage_broadcasts: bool = True,
    can_view_sessions: bool = True,
    can_ban_users: bool = True,
    can_manage_sub: bool = True,
) -> InlineKeyboardMarkup:
    """
    Главная клавиатура панели администратора с учетом прав доступа.
    """
    keyboard = []

    # 1. Режим доступа
    if is_super_admin or can_manage_wl:
        mode_title = "🔒 Режим: Только Whitelist" if access_mode == "whitelist" else "🌐 Режим: Доступ всем"
        keyboard.append([
            InlineKeyboardButton(text=mode_title, callback_data="admin_toggle_access")
        ])
        keyboard.append([
            InlineKeyboardButton(text="➕ Добавить в Whitelist", callback_data="admin_wl_add"),
            InlineKeyboardButton(text="➖ Удалить из Whitelist", callback_data="admin_wl_del"),
        ])
        keyboard.append([
            InlineKeyboardButton(text="📋 Список Whitelist", callback_data="admin_wl_list"),
        ])

    # 2. Мониторинг рассылок и сессий
    row_mon = []
    if is_super_admin or can_manage_broadcasts:
        row_mon.append(InlineKeyboardButton(text="🚀 Рассылки", callback_data="admin_tasks_list"))
    if is_super_admin or can_view_sessions:
        row_mon.append(InlineKeyboardButton(text="📱 Сессии", callback_data="admin_sessions_list"))
    if row_mon:
        keyboard.append(row_mon)

    # 3. Бан-лист и блокировка
    if is_super_admin or can_ban_users:
        keyboard.append([
            InlineKeyboardButton(text="🚫 Заблокировать", callback_data="admin_ban_user_prompt"),
            InlineKeyboardButton(text="📋 Бан-лист", callback_data="admin_banned_list"),
        ])

    # 4. Управление подпиской
    if is_super_admin or can_manage_sub:
        keyboard.append([
            InlineKeyboardButton(text="📢 Обязательная / Авто-подписка", callback_data="admin_sub_menu"),
        ])

    # 5. Раздел Главного Администратора: Управление админами и бэкап
    if is_super_admin:
        keyboard.append([
            InlineKeyboardButton(text="👥 Администраторы", callback_data="admin_admins_list"),
            InlineKeyboardButton(text="💾 Скачать бэкап БД", callback_data="admin_download_db"),
        ])

    keyboard.append([
        InlineKeyboardButton(text="⬅️ В главное меню", callback_data="menu_main")
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_ban_durations_kb() -> InlineKeyboardMarkup:
    """
    Выбор срока блокировки.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⏱ 1 час", callback_data="ban_dur:60"),
                InlineKeyboardButton(text="⏱ 24 часа", callback_data="ban_dur:1440"),
            ],
            [
                InlineKeyboardButton(text="⏱ 7 дней", callback_data="ban_dur:10080"),
                InlineKeyboardButton(text="⏱ 30 дней", callback_data="ban_dur:43200"),
            ],
            [
                InlineKeyboardButton(text="♾ Навсегда", callback_data="ban_dur:forever"),
            ],
            [
                InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel")
            ]
        ]
    )

def get_banned_list_kb(banned: List[BannedUser]) -> InlineKeyboardMarkup:
    """
    Список заблокированных пользователей с кнопками разбана.
    """
    keyboard = []
    for b in banned:
        uname = f"@{b.username}" if b.username else f"ID: {b.telegram_id}"
        dur = "навсегда" if not b.banned_until else f"до {b.banned_until.strftime('%d.%m')}"
        keyboard.append([
            InlineKeyboardButton(
                text=f"🚫 {uname} ({dur})",
                callback_data=f"banned_info:{b.telegram_id}"
            ),
            InlineKeyboardButton(
                text="🔓 Разбанить",
                callback_data=f"admin_unban:{b.telegram_id}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(text="➕ Заблокировать пользователя", callback_data="admin_ban_user_prompt")
    ])
    keyboard.append([
        InlineKeyboardButton(text="⬅️ В панель админа", callback_data="menu_admin")
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_admins_list_kb(admins: List[AdminUser]) -> InlineKeyboardMarkup:
    """
    Список назначенных администраторов.
    """
    keyboard = []
    for a in admins:
        uname = f"@{a.username}" if a.username else f"ID: {a.telegram_id}"
        keyboard.append([
            InlineKeyboardButton(
                text=f"👤 {uname}",
                callback_data=f"admin_perms:{a.telegram_id}"
            ),
            InlineKeyboardButton(
                text="❌ Снять",
                callback_data=f"admin_remove:{a.telegram_id}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(text="➕ Назначить администратора", callback_data="admin_add_prompt")
    ])
    keyboard.append([
        InlineKeyboardButton(text="⬅️ В панель админа", callback_data="menu_admin")
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_admin_perms_kb(admin: AdminUser) -> InlineKeyboardMarkup:
    """
    Интерактивная панель настройки прав для конкретного администратора.
    """
    def check(val: bool) -> str:
        return "✅" if val else "❌"

    keyboard = [
        [
            InlineKeyboardButton(
                text=f"{check(admin.can_manage_whitelist)} Управление Whitelist",
                callback_data=f"tperm:{admin.telegram_id}:can_manage_whitelist"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{check(admin.can_manage_broadcasts)} Управление рассылками",
                callback_data=f"tperm:{admin.telegram_id}:can_manage_broadcasts"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{check(admin.can_view_sessions)} Просмотр сессий и кодов",
                callback_data=f"tperm:{admin.telegram_id}:can_view_sessions"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{check(admin.can_ban_users)} Блокировка пользователей",
                callback_data=f"tperm:{admin.telegram_id}:can_ban_users"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{check(admin.can_manage_sub)} Обязательная подписка (ОП)",
                callback_data=f"tperm:{admin.telegram_id}:can_manage_sub"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{check(admin.can_manage_anon)} Модуль @AnonRuBot",
                callback_data=f"tperm:{admin.telegram_id}:can_manage_anon"
            )
        ],
        [
            InlineKeyboardButton(text="⬅️ К списку администраторов", callback_data="admin_admins_list")
        ]
    ]

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_admin_back_kb() -> InlineKeyboardMarkup:
    """
    Кнопка возврата в меню администратора.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ В панель админа", callback_data="menu_admin")]
        ]
    )

def get_cancel_admin_kb() -> InlineKeyboardMarkup:
    """
    Кнопка отмены ввода в админке.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel")]
        ]
    )
