from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import List, Dict

def get_subscription_gate_kb(channels: List[Dict[str, str]]) -> InlineKeyboardMarkup:
    """
    Клавиатура с кнопками-ссылками на обязательные каналы и кнопкой проверки.
    """
    keyboard = []

    for ch in channels:
        title = ch.get("title", "Канал")
        url = ch.get("url", f"https://t.me/{ch.get('username', '')}")
        keyboard.append([
            InlineKeyboardButton(text=f"📢 {title}", url=url)
        ])

    keyboard.append([
        InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_subscription")
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_admin_auto_sub_kb(gate_enabled: bool, mtproto_enabled: bool, channels: List[Dict[str, str]]) -> InlineKeyboardMarkup:
    """
    Клавиатура управления разделом «Авто-подписка» в панели администратора.
    """
    gate_badge = "🟢 Включена" if gate_enabled else "🔴 Выключена"
    mtproto_badge = "🟢 Включена" if mtproto_enabled else "🔴 Выключена"

    keyboard = [
        [
            InlineKeyboardButton(
                text=f"📢 Проверка при /start: {gate_badge}",
                callback_data="admin_toggle_sub_gate"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"🤖 Авто-подписка аккаунтов: {mtproto_badge}",
                callback_data="admin_toggle_sub_mtproto"
            )
        ],
        [
            InlineKeyboardButton(
                text="⚡️ Подписать все сессии сейчас",
                callback_data="admin_join_all_sessions"
            )
        ]
    ]

    # Кнопки каналов для удаления
    if channels:
        for ch in channels:
            title = ch.get("title", ch.get("username", "Канал"))
            uname = ch.get("username", "")
            keyboard.append([
                InlineKeyboardButton(text=f"📢 {title}", url=ch.get("url", f"https://t.me/{uname}")),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin_sub_del:{uname}"),
            ])

    keyboard.append([
        InlineKeyboardButton(text="➕ Добавить канал в ОП", callback_data="admin_sub_add_prompt")
    ])

    keyboard.append([
        InlineKeyboardButton(text="⬅️ В панель админа", callback_data="menu_admin")
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)
