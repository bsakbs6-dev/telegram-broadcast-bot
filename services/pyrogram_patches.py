"""
Модуль патчей для Pyrogram:
1. Поддержка 64-битных ID каналов (MIN_CHANNEL_ID).
2. Безопасная пагинация dialogs (get_dialogs) с защитой от 'NoneType' object has no attribute 'id'.
   В оригинальном Pyrogram, если последний диалог в пачке пуст (нет сообщений / очищен чат / пустой канал),
   last.top_message равен None, и вызов last.top_message.id вызывает AttributeError.
"""

import logging
from typing import AsyncGenerator, Optional
import pyrogram
from pyrogram import types, raw, utils

logger = logging.getLogger(__name__)

async def safe_get_dialogs(
    self: "pyrogram.Client",
    limit: int = 0
) -> AsyncGenerator["types.Dialog", None]:
    """
    Безопасная версия Client.get_dialogs, исправляющая баг Pyrogram:
    AttributeError: 'NoneType' object has no attribute 'id'
    когда чат или канал не имеет сообщений (top_message is None).
    """
    current = 0
    total = limit or (1 << 31) - 1
    batch_limit = min(100, total)

    offset_date = 0
    offset_id = 0
    offset_peer = raw.types.InputPeerEmpty()

    while True:
        try:
            r = await self.invoke(
                raw.functions.messages.GetDialogs(
                    offset_date=offset_date,
                    offset_id=offset_id,
                    offset_peer=offset_peer,
                    limit=batch_limit,
                    hash=0
                ),
                sleep_threshold=60
            )
        except Exception as e:
            logger.warning(f"GetDialogs invoke error: {e}")
            return

        users = {i.id: i for i in r.users}
        chats = {i.id: i for i in r.chats}

        messages = {}
        for message in r.messages:
            if isinstance(message, raw.types.MessageEmpty):
                continue
            try:
                chat_id = utils.get_peer_id(message.peer_id)
                messages[chat_id] = await types.Message._parse(self, message, users, chats)
            except Exception:
                pass

        dialogs = []
        for dialog in r.dialogs:
            if not isinstance(dialog, raw.types.Dialog):
                continue
            try:
                parsed_dialog = types.Dialog._parse(self, dialog, messages, users, chats)
                if parsed_dialog and parsed_dialog.chat:
                    dialogs.append(parsed_dialog)
            except Exception:
                pass

        if not dialogs:
            return

        # 1. Сначала отдаем все полученные диалоги
        for dialog in dialogs:
            yield dialog
            current += 1
            if current >= total:
                return

        # Если Telegram вернул меньше диалогов, чем запрашивалось — список исчерпан
        if len(r.dialogs) < batch_limit:
            return

        # 2. Вычисляем смещение для следующей пачки
        # Ищем с конца последний диалог с валидным сообщением (top_message)
        last_with_msg = next(
            (d for d in reversed(dialogs) if d.top_message and getattr(d.top_message, "id", None) and getattr(d.top_message, "date", None)),
            None
        )

        try:
            if last_with_msg and last_with_msg.top_message and last_with_msg.chat:
                offset_id = last_with_msg.top_message.id
                offset_date = utils.datetime_to_timestamp(last_with_msg.top_message.date)
                offset_peer = await self.resolve_peer(last_with_msg.chat.id)
            else:
                last = dialogs[-1]
                if last and last.chat:
                    offset_id = getattr(last.top_message, "id", 0) or 0
                    msg_date = getattr(last.top_message, "date", None)
                    offset_date = utils.datetime_to_timestamp(msg_date) if msg_date else 0
                    offset_peer = await self.resolve_peer(last.chat.id)
                else:
                    return
        except Exception as e:
            logger.warning(f"Failed to calculate next offset in get_dialogs: {e}")
            return


_patches_applied = False

def apply_pyrogram_patches():
    """
    Применяет все необходимые патчи к библиотеке Pyrogram.
    Вызывается автоматически при импорте модуля.
    """
    global _patches_applied
    if _patches_applied:
        return

    # 1. 64-битные ID каналов
    pyrogram.utils.MIN_CHANNEL_ID = -1009999999999999

    # 2. Исправление пагинации get_dialogs
    pyrogram.Client.get_dialogs = safe_get_dialogs
    try:
        import pyrogram.methods.chats.get_dialogs as gd
        gd.GetDialogs.get_dialogs = safe_get_dialogs
    except Exception:
        pass

    _patches_applied = True
    logger.debug("Pyrogram patches applied successfully.")

# Применяем патчи при первой загрузке модуля
apply_pyrogram_patches()
