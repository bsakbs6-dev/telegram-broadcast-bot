"""
Multi-Tenant Broadcast Telegram Bot Package
"""
import pyrogram.utils

# Патч для поддержки новых 64-битных Telegram Channel ID в Pyrogram
pyrogram.utils.MIN_CHANNEL_ID = -1009999999999999
