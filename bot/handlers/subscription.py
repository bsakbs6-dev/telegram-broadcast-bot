import logging
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from bot.config import config
from bot.services.subscription_service import subscription_service
from bot.keyboards.subscription_kb import get_subscription_gate_kb
from bot.keyboards.main_kb import get_main_menu_kb
from bot.handlers.start import render_main_menu_text

router = Router(name="subscription_router")
logger = logging.getLogger(__name__)

@router.callback_query(F.data == "check_subscription")
async def cb_check_subscription(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    Проверка подписки на обязательные каналы по кнопке «✅ Проверить подписку».
    """
    user_id = callback.from_user.id
    is_admin = (user_id == config.ADMIN_ID)

    is_subscribed, unsubscribed = await subscription_service.check_user_subscription(bot, user_id)

    if not is_subscribed:
        unsub_names = ", ".join([ch.get("title", ch.get("username", "Канал")) for ch in unsubscribed])
        await callback.answer(
            f"❌ Вы еще не подписались на:\n{unsub_names}\n\nПожалуйста, подпишитесь на все каналы выше!",
            show_alert=True
        )
        return

    await callback.answer("✅ Спасибо за подписку! Доступ открыт.", show_alert=True)
    await state.clear()

    # Показываем главное меню
    text = await render_main_menu_text(user_id)
    kb = get_main_menu_kb(is_admin=is_admin)

    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
