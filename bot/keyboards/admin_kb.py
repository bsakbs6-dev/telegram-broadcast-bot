from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def get_admin_main_kb(access_mode: str) -> InlineKeyboardMarkup:
    """
    Главная клавиатура панели администратора.
    """
    mode_title = "🔒 Режим: Только Whitelist" if access_mode == "whitelist" else "🌐 Режим: Доступ всем"

    keyboard = [
        [
            InlineKeyboardButton(text=mode_title, callback_data="admin_toggle_access")
        ],
        [
            InlineKeyboardButton(text="➕ Добавить в Whitelist", callback_data="admin_wl_add"),
            InlineKeyboardButton(text="➖ Удалить из Whitelist", callback_data="admin_wl_del"),
        ],
        [
            InlineKeyboardButton(text="📋 Список Whitelist", callback_data="admin_wl_list"),
        ],
        [
            InlineKeyboardButton(text="🚀 Активные рассылки", callback_data="admin_tasks_list"),
            InlineKeyboardButton(text="📱 Сессии пользователей", callback_data="admin_sessions_list"),
        ],
        [
            InlineKeyboardButton(text="📢 Обязательная / Авто-подписка", callback_data="admin_sub_menu"),
        ],
        [
            InlineKeyboardButton(text="💾 Скачать бэкап БД (.db)", callback_data="admin_download_db"),
        ],
        [
            InlineKeyboardButton(text="⬅️ В главное меню", callback_data="menu_main")
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
