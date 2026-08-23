from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def get_settings_kb(
    interval_minutes: float,
    rotation_mode: str,
    jitter_min: int,
    jitter_max: int,
) -> InlineKeyboardMarkup:
    """
    Клавиатура настройки параметров рассылки.
    """
    rot_text = "🔀 Случайно (Random)" if rotation_mode == "random" else "🔁 По очереди (Round-Robin)"

    keyboard = [
        # Быстрый выбор интервала
        [
            InlineKeyboardButton(
                text=f"{'🔘' if interval_minutes == 0.5 else '⚪️'} 30 сек",
                callback_data="set_interval:0.5"
            ),
            InlineKeyboardButton(
                text=f"{'🔘' if interval_minutes == 1.0 else '⚪️'} 1 мин",
                callback_data="set_interval:1"
            ),
            InlineKeyboardButton(
                text=f"{'🔘' if interval_minutes == 5.0 else '⚪️'} 5 мин",
                callback_data="set_interval:5"
            ),
        ],
        [
            InlineKeyboardButton(
                text=f"{'🔘' if interval_minutes == 15.0 else '⚪️'} 15 мин",
                callback_data="set_interval:15"
            ),
            InlineKeyboardButton(
                text=f"{'🔘' if interval_minutes == 30.0 else '⚪️'} 30 мин",
                callback_data="set_interval:30"
            ),
            InlineKeyboardButton(
                text="✍️ Свой интервал",
                callback_data="set_interval_custom"
            ),
        ],
        # Переключение ротации
        [
            InlineKeyboardButton(
                text=f"🔄 Ротация: {rot_text}",
                callback_data="set_toggle_rotation"
            ),
        ],
        # Назад
        [
            InlineKeyboardButton(text="⬅️ В главное меню", callback_data="menu_main")
        ],
    ]

    return InlineKeyboardMarkup(inline_keyboard=keyboard)
