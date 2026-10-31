from typing import Dict
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from bot.database.models import BroadcastMessage

def get_messages_pool_kb(messages_by_slot: Dict[int, BroadcastMessage]) -> InlineKeyboardMarkup:
    """
    Клавиатура управления пулом из 4 слотов сообщений.
    """
    keyboard = []

    for slot in range(1, 5):
        msg = messages_by_slot.get(slot)
        if msg:
            preview = (msg.text[:20] + "..") if len(msg.text) > 20 else msg.text
            preview = preview.replace("\n", " ")
            keyboard.append([
                InlineKeyboardButton(text=f"✏️ Слот {slot}: {preview}", callback_data=f"msg_edit:{slot}"),
                InlineKeyboardButton(text="🗑", callback_data=f"msg_del:{slot}"),
            ])
        else:
            keyboard.append([
                InlineKeyboardButton(text=f"➕ Добавить текст {slot}", callback_data=f"msg_add:{slot}")
            ])

    keyboard.append([
        InlineKeyboardButton(text="⬅️ В главное меню", callback_data="menu_main")
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_cancel_message_kb() -> InlineKeyboardMarkup:
    """
    Клавиатура отмены ввода сообщения.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="msg_cancel")]
        ]
    )
