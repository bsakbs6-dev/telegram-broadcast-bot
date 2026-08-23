import math
from typing import List
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from bot.database.models import TargetChannel

PAGE_SIZE = 6

def get_channels_pagination_kb(
    channels: List[TargetChannel],
    page: int = 0,
) -> InlineKeyboardMarkup:
    """
    Инлайн-клавиатура со списком каналов, чекбоксами (✅/❌) и пагинацией.
    """
    total_channels = len(channels)
    total_pages = max(1, math.ceil(total_channels / PAGE_SIZE))
    current_page = max(0, min(page, total_pages - 1))

    start_idx = current_page * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    page_channels = channels[start_idx:end_idx]

    keyboard = []

    # 1. Список каналов на текущей странице
    for chan in page_channels:
        status_icon = "✅" if chan.is_selected else "❌"
        # Ограничиваем длину названия кнопки
        title = (chan.title[:24] + "...") if len(chan.title) > 24 else chan.title
        btn_text = f"{status_icon} {title}"
        callback_data = f"chan_tog:{chan.id}:{current_page}"
        keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=callback_data)])

    # 2. Быстрые действия: Выбрать все / Снять выбор
    keyboard.append([
        InlineKeyboardButton(text="🔘 Выбрать все", callback_data=f"chan_all:{current_page}"),
        InlineKeyboardButton(text="⚪️ Снять выбор", callback_data=f"chan_none:{current_page}"),
    ])

    # 3. Панель пагинации (◀️ Стр. X/Y ▶️)
    nav_row = []
    if current_page > 0:
        nav_row.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"chan_page:{current_page - 1}"))
    else:
        nav_row.append(InlineKeyboardButton(text="⏹", callback_data="noop"))

    nav_row.append(InlineKeyboardButton(text=f"📄 {current_page + 1}/{total_pages}", callback_data="noop"))

    if current_page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"chan_page:{current_page + 1}"))
    else:
        nav_row.append(InlineKeyboardButton(text="⏹", callback_data="noop"))

    keyboard.append(nav_row)

    # 4. Пересканировать и Выход
    keyboard.append([
        InlineKeyboardButton(text="🔄 Обновить список (MTProto)", callback_data="channels_rescan"),
    ])
    keyboard.append([
        InlineKeyboardButton(text="⬅️ В главное меню", callback_data="menu_main")
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)
