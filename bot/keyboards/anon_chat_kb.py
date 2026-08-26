from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def get_anon_chat_kb(is_running: bool, trigger_symbol: str) -> InlineKeyboardMarkup:
    """
    Клавиатура управления модулем анонимного чата (@AnonRuBot).
    """
    keyboard = []

    # Кнопка старт / стоп
    if is_running:
        keyboard.append([
            InlineKeyboardButton(
                text="⏸ Остановить авто-чат",
                callback_data="anon_stop"
            )
        ])
    else:
        keyboard.append([
            InlineKeyboardButton(
                text="▶️ Запустить авто-чат (@AnonRuBot)",
                callback_data="anon_start"
            )
        ])

    # Настройки
    keyboard.append([
        InlineKeyboardButton(
            text="📝 Изменить текст сообщения",
            callback_data="anon_edit_text"
        )
    ])
    keyboard.append([
        InlineKeyboardButton(
            text=f"🎯 Триггер лида: [{trigger_symbol}]",
            callback_data="anon_edit_trigger"
        ),
        InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data="menu_anon_chat"
        )
    ])
    keyboard.append([
        InlineKeyboardButton(
            text="📊 Сбросить статистику",
            callback_data="anon_reset_stats"
        )
    ])
    keyboard.append([
        InlineKeyboardButton(
            text="⬅️ В главное меню",
            callback_data="menu_main"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_cancel_anon_kb() -> InlineKeyboardMarkup:
    """
    Кнопка отмены редактирования текста или триггера.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="anon_cancel"
                )
            ]
        ]
    )
