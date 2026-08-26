from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def get_main_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    """
    Главное инлайн-меню бота.
    """
    keyboard = [
        [
            InlineKeyboardButton(text="📱 Мой аккаунт", callback_data="menu_account"),
            InlineKeyboardButton(text="✉️ Пул сообщений", callback_data="menu_messages"),
        ],
        [
            InlineKeyboardButton(text="📢 Выбор каналов", callback_data="menu_channels:0"),
            InlineKeyboardButton(text="⚙️ Настройки цикла", callback_data="menu_settings"),
        ],
        [
            InlineKeyboardButton(text="🚀 Управление рассылкой", callback_data="menu_control"),
        ],
        [
            InlineKeyboardButton(text="🎭 Анонимный чат (@AnonRuBot)", callback_data="menu_anon_chat"),
        ],
    ]

    if is_admin:
        keyboard.append([
            InlineKeyboardButton(text="👑 Админ-панель", callback_data="menu_admin")
        ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_back_to_main_kb() -> InlineKeyboardMarkup:
    """
    Кнопка быстрого возврата в главное меню.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ В главное меню", callback_data="menu_main")]
        ]
    )
