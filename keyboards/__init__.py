"""
Keyboards package
"""
from bot.keyboards.main_kb import get_main_menu_kb, get_back_to_main_kb
from bot.keyboards.auth_kb import get_account_kb, get_cancel_auth_kb
from bot.keyboards.channels_kb import get_channels_pagination_kb
from bot.keyboards.messages_kb import get_messages_pool_kb, get_cancel_message_kb
from bot.keyboards.settings_kb import get_settings_kb
from bot.keyboards.admin_kb import get_admin_main_kb, get_admin_back_kb

__all__ = [
    "get_main_menu_kb",
    "get_back_to_main_kb",
    "get_account_kb",
    "get_cancel_auth_kb",
    "get_channels_pagination_kb",
    "get_messages_pool_kb",
    "get_cancel_message_kb",
    "get_settings_kb",
    "get_admin_main_kb",
    "get_admin_back_kb",
]
