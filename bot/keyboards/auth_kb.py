from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

def get_account_kb(has_session: bool) -> InlineKeyboardMarkup:
    """
    Клавиатура управления профилем и подключением Telegram-аккаунта.
    """
    keyboard = []

    if not has_session:
        keyboard.append([
            InlineKeyboardButton(text="🔑 Подключить аккаунт", callback_data="auth_start")
        ])
    else:
        keyboard.append([
            InlineKeyboardButton(text="🔄 Переподключить аккаунт", callback_data="auth_start"),
            InlineKeyboardButton(text="🚪 Выйти из аккаунта", callback_data="auth_logout"),
        ])
        keyboard.append([
            InlineKeyboardButton(text="🔄 Обновить список каналов", callback_data="channels_rescan"),
        ])

    keyboard.append([
        InlineKeyboardButton(text="⬅️ В главное меню", callback_data="menu_main")
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_cancel_auth_kb() -> InlineKeyboardMarkup:
    """
    Кнопка отмены процесса авторизации.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить вход", callback_data="auth_cancel")]
        ]
    )

def get_waiting_code_kb() -> InlineKeyboardMarkup:
    """
    Клавиатура ожидания кода с возможностью запросить код по SMS или отменить.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📩 Запросить код по SMS", callback_data="auth_resend_sms")],
            [InlineKeyboardButton(text="❌ Отменить вход", callback_data="auth_cancel")],
        ]
    )

def get_share_phone_reply_kb() -> ReplyKeyboardMarkup:
    """
    Быстрая кнопка под полем ввода для отправки своего номера телефона в 1 клик.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📱 Отправить мой номер телефона", request_contact=True)
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

