import logging
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

from bot.config import config
from bot.database.db import get_session, get_or_create_user, is_user_allowed, check_user_ban

logger = logging.getLogger(__name__)

class AccessMiddleware(BaseMiddleware):
    """
    Middleware проверки прав доступа к боту:
    - Пропускает администратора (config.ADMIN_ID).
    - Проверяет глобальный режим доступа (public / whitelist).
    - Проверяет наличие пользователя в таблице Whitelist.
    - Автоматически синхронизирует профиль пользователя в таблице users.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)

        telegram_id = user.id
        username = user.username
        first_name = user.first_name

        async with get_session() as session:
            # 0. Проверяем наличие активного бана
            is_banned, ban_reason, banned_until = await check_user_ban(session, telegram_id)
            if is_banned:
                duration_str = f"до {banned_until.strftime('%d.%m.%Y %H:%M UTC')}" if banned_until else "НАВСЕГДА ♾"
                ban_text = (
                    "🚫 <b>Ваш доступ к боту заблокирован!</b>\n\n"
                    f"📄 <b>Причина:</b> <i>{ban_reason or 'Нарушение правил'}</i>\n"
                    f"⏳ <b>Срок блокировки:</b> <code>{duration_str}</code>\n\n"
                    "<i>По вопросам разблокировки обратитесь к главному администратору.</i>"
                )
                if isinstance(event, Message):
                    await event.answer(ban_text, parse_mode="HTML")
                elif isinstance(event, CallbackQuery):
                    await event.answer("🚫 Вы заблокированы в боте!", show_alert=True)
                    if event.message:
                        try:
                            await event.message.edit_text(ban_text, parse_mode="HTML")
                        except Exception:
                            pass
                return None

            # Создаем или обновляем пользователя в базе данных
            db_user = await get_or_create_user(
                session=session,
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
            )
            data["db_user"] = db_user

            # Проверяем доступ
            allowed = await is_user_allowed(
                session=session,
                telegram_id=telegram_id,
                username=username,
            )

        if allowed:
            return await handler(event, data)

        # Если доступ закрыт:
        logger.warning(f"Access denied for user {telegram_id} (@{username})")

        denied_text = (
            "⛔️ <b>Доступ ограничен</b>\n\n"
            "Бот находится в закрытом режиме и доступен только пользователям из Белого списка (Whitelist).\n\n"
            f"Ваш Telegram ID: <code>{telegram_id}</code>\n"
            f"Ваш логин: @{username or 'не_указан'}\n\n"
            "Для получения доступа обратитесь к администратору."
        )

        if isinstance(event, Message):
            await event.answer(denied_text, parse_mode="HTML")
        elif isinstance(event, CallbackQuery):
            await event.answer("⛔️ Доступ ограничен. Обратитесь к администратору.", show_alert=True)
            if event.message:
                try:
                    await event.message.edit_text(denied_text, parse_mode="HTML")
                except Exception:
                    pass

        return None
