import re
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, update, delete

from bot.database.db import get_session
from bot.database.models import UserSession, TargetChannel
from bot.states.auth_states import AuthStates
from bot.keyboards.auth_kb import (
    get_account_kb,
    get_cancel_auth_kb,
    get_waiting_code_kb,
    get_share_phone_reply_kb,
)
from bot.keyboards.main_kb import get_back_to_main_kb
from bot.services.auth_manager import auth_manager
from bot.services.telegram_client import TelegramClientService
from bot.services.broadcaster import broadcaster
from bot.services.subscription_service import subscription_service

router = Router(name="auth_router")
logger = logging.getLogger(__name__)

@router.callback_query(F.data == "menu_account")
async def cb_account_menu(callback: CallbackQuery, state: FSMContext):
    """
    Отображение профиля и статуса подключения Telegram-аккаунта.
    """
    await state.clear()
    user_id = callback.from_user.id

    async with get_session() as session:
        stmt = select(UserSession).where(UserSession.user_id == user_id, UserSession.is_active == True)
        user_sess = (await session.execute(stmt)).scalar_one_or_none()

    if user_sess:
        text = (
            "📱 <b>Информация о подключенном аккаунте</b>\n\n"
            f"👤 <b>Имя:</b> {user_sess.first_name or 'Не указано'}\n"
            f"🔗 <b>Username:</b> @{user_sess.username or 'отсутствует'}\n"
            f"📞 <b>Номер телефона:</b> <code>{user_sess.phone_number}</code>\n"
            f"🟢 <b>Статус:</b> Активен\n"
            f"📅 <b>Подключен:</b> {user_sess.updated_at.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            "<i>Вы можете обновить список каналов или выйти из аккаунта.</i>"
        )
        kb = get_account_kb(has_session=True)
    else:
        text = (
            "📱 <b>Telegram-аккаунт не подключен</b>\n\n"
            "Для автоматической рассылки сообщений необходимо подключить ваш аккаунт через защищенную MTProto сессию.\n\n"
            "📌 <b>Вам потребуется:</b>\n"
            "1. Ввести номер телефона.\n"
            "2. Ввести код подтверждения (с точкой или пробелом: <code>12.345</code>).\n"
            "3. Ввести облачный пароль 2FA (если он установлен).\n\n"
            "<i>Все данные изолированы и сохраняются в зашифрованном виде.</i>"
        )
        kb = get_account_kb(has_session=False)

    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "auth_start")
async def cb_auth_start(callback: CallbackQuery, state: FSMContext):
    """
    Начало пошагового FSM входа: запрос номера телефона.
    """
    await state.set_state(AuthStates.waiting_for_phone)
    text = (
        "🔑 <b>Подключение Telegram-аккаунта (Шаг 1 из 3)</b>\n\n"
        "Нажмите кнопку <b>«📱 Отправить мой номер телефона»</b> внизу экрана или введите номер вручную:\n"
        "👉 Например: <code>+79991234567</code> или <code>+380991234567</code>\n\n"
        "<i>Используйте кнопку ниже для отмены.</i>"
    )
    await callback.message.edit_text(text, reply_markup=get_cancel_auth_kb(), parse_mode="HTML")
    # Отправляем Reply кнопку для отправки контакта в 1 клик
    await callback.message.answer(
        "👇 <b>Нажмите кнопку внизу для отправки номера в 1 клик:</b>",
        reply_markup=get_share_phone_reply_kb(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data == "auth_cancel")
async def cb_auth_cancel(callback: CallbackQuery, state: FSMContext):
    """
    Отмена процесса авторизации.
    """
    user_id = callback.from_user.id
    await auth_manager.cleanup(user_id)
    await state.clear()

    await callback.message.edit_text(
        "❌ <b>Авторизация отменена.</b>",
        reply_markup=get_back_to_main_kb(),
        parse_mode="HTML"
    )
    # Убираем Reply кнопку
    try:
        msg_rm = await callback.message.answer("...", reply_markup=ReplyKeyboardRemove())
        await msg_rm.delete()
    except Exception:
        pass
    await callback.answer()

@router.callback_query(F.data == "auth_resend_sms")
async def cb_auth_resend_sms(callback: CallbackQuery, state: FSMContext):
    """
    Повторная отправка кода по SMS.
    """
    user_id = callback.from_user.id
    success, res_msg = await auth_manager.resend_code(user_id)
    if success:
        await callback.answer("📩 Telegram отправил код по SMS!", show_alert=True)
    else:
        await callback.answer(res_msg, show_alert=True)

@router.message(AuthStates.waiting_for_phone, F.contact | F.text)
async def process_phone_input(message: Message, state: FSMContext):
    """
    Обработка ввода номера телефона (кнопкой «Поделиться контактом» или текстом).
    """
    if message.contact:
        raw_phone = message.contact.phone_number or ""
    else:
        raw_phone = message.text.strip() if message.text else ""

    # Очистка номера от пробелов, скобок, дефисов
    phone_digits = re.sub(r'\D', '', raw_phone)

    if len(phone_digits) < 10 or len(phone_digits) > 15:
        await message.answer(
            "❌ <b>Некорректный формат номера!</b>\n\n"
            "Пожалуйста, нажмите кнопку <b>«📱 Отправить мой номер телефона»</b> внизу или введите номер с кодом страны (например: <code>+79991234567</code>):",
            reply_markup=get_share_phone_reply_kb(),
            parse_mode="HTML"
        )
        return

    phone_number = f"+{phone_digits}"
    user_id = message.from_user.id

    msg_wait = await message.answer(
        "⏳ <i>Отправка запроса в Telegram...</i>",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="HTML"
    )

    success, res_text = await auth_manager.start_auth(user_id, phone_number)

    if not success:
        await msg_wait.edit_text(
            f"{res_text}\n\nПопробуйте еще раз или нажмите отмену:",
            reply_markup=get_cancel_auth_kb(),
            parse_mode="HTML"
        )
        return

    await state.update_data(phone_number=phone_number)
    await state.set_state(AuthStates.waiting_for_code)

    code_instruction = (
        f"📲 <b>Код подтверждения отправлен {res_text}! (Шаг 2 из 3)</b>\n\n"
        "⚠️ <b>ВНИМАНИЕ (ВАЖНО):</b> Чтобы Telegram не перехватил и не заблокировал сервисное сообщение в боте, "
        "введите 5-значный код <b>с точкой, пробелом или дефисом</b>:\n\n"
        "👉 Например: <code>12.345</code> или <code>12 345</code> или <code>12-345</code>\n\n"
        "💡 <i>Если код не появился в чате Telegram на ПК, проверьте мобильное приложение Telegram на смартфоне или нажмите «📩 Запросить код по SMS» ниже.</i>"
    )
    await msg_wait.edit_text(code_instruction, reply_markup=get_waiting_code_kb(), parse_mode="HTML")

@router.message(AuthStates.waiting_for_code)
async def process_code_input(message: Message, state: FSMContext):
    """
    Обработка ввода кода подтверждения с очисткой разделителей.
    """
    user_id = message.from_user.id
    raw_code = message.text or ""

    # Очищаем код от любых нецифровых символов (точек, пробелов, дефисов)
    clean_code = re.sub(r'\D', '', raw_code)

    if len(clean_code) != 5:
        await message.answer(
            "❌ <b>Неверный формат кода!</b>\n\n"
            "Код подтверждения Telegram должен содержать ровно <b>5 цифр</b>.\n"
            "Введите код с разделителем, например: <code>12.345</code>",
            reply_markup=get_cancel_auth_kb(),
            parse_mode="HTML"
        )
        return

    msg_wait = await message.answer("⏳ <i>Проверка кода...</i>", parse_mode="HTML")

    status, result, me = await auth_manager.submit_code(user_id, clean_code)

    if status == "need_2fa":
        await state.set_state(AuthStates.waiting_for_2fa)
        text_2fa = (
            "🔐 <b>Двухфакторная аутентификация (2FA) (Шаг 3 из 3)</b>\n\n"
            "На вашем аккаунте включен облачный пароль.\n"
            "Пожалуйста, введите ваш пароль 2FA:"
        )
        await msg_wait.edit_text(text_2fa, reply_markup=get_cancel_auth_kb(), parse_mode="HTML")
        return

    elif status == "error":
        await msg_wait.edit_text(
            f"{result}\n\nПопробуйте ввести код еще раз (с точкой, например <code>12.345</code>):",
            reply_markup=get_cancel_auth_kb(),
            parse_mode="HTML"
        )
        return

    elif status == "success":
        # Авторизация завершена
        session_string = result
        await complete_authorization(user_id, session_string, me, state, msg_wait)

@router.message(AuthStates.waiting_for_2fa)
async def process_2fa_input(message: Message, state: FSMContext):
    """
    Обработка ввода 2FA облачного пароля.
    """
    user_id = message.from_user.id
    password = message.text.strip() if message.text else ""

    # Удаляем сообщение с паролем из чата ради безопасности
    try:
        await message.delete()
    except Exception:
        pass

    msg_wait = await message.answer("⏳ <i>Проверка 2FA пароля...</i>", parse_mode="HTML")

    success, result, me = await auth_manager.submit_password(user_id, password)

    if not success:
        await msg_wait.edit_text(
            f"{result}\n\nВведите пароль еще раз:",
            reply_markup=get_cancel_auth_kb(),
            parse_mode="HTML"
        )
        return

    session_string = result
    await state.update_data(two_factor_password=password)
    await complete_authorization(user_id, session_string, me, state, msg_wait)

async def complete_authorization(user_id: int, session_string: str, me: any, state: FSMContext, status_msg: Message):
    """
    Финализация успешной авторизации: сохранение StringSession и 2FA пароля в БД и сканирование каналов.
    """
    data = await state.get_data()
    phone_number = data.get("phone_number", me.phone_number if hasattr(me, "phone_number") else "")
    two_factor_password = data.get("two_factor_password", None)

    await state.clear()

    # Сохраняем сессию в БД
    async with get_session() as session:
        sess_stmt = select(UserSession).where(UserSession.user_id == user_id)
        existing = (await session.execute(sess_stmt)).scalar_one_or_none()

        first_name = me.first_name or ""
        username = me.username or ""

        if existing:
            existing.phone_number = phone_number
            existing.session_string = session_string
            if two_factor_password:
                existing.two_factor_password = two_factor_password
            existing.first_name = first_name
            existing.username = username
            existing.is_active = True
        else:
            session.add(UserSession(
                user_id=user_id,
                phone_number=phone_number,
                session_string=session_string,
                two_factor_password=two_factor_password,
                first_name=first_name,
                username=username,
                is_active=True,
            ))

    await status_msg.edit_text(
        f"✅ <b>Аккаунт успешно подключен!</b>\n\n"
        f"👤 <b>{first_name}</b> (@{username or 'не указан'})\n"
        f"📞 <code>{phone_number}</code>\n\n"
        f"🔄 <i>Запускается первичное сканирование каналов и групп...</i>",
        parse_mode="HTML"
    )

    # Запускаем авто-подписку аккаунта на каналы (в фоне)
    if await subscription_service.is_mtproto_auto_sub_enabled():
        asyncio.create_task(subscription_service.auto_join_account(session_string))

    # Сканируем доступные каналы
    count, scan_msg = await TelegramClientService.scan_and_save_dialogs(user_id, session_string)

    final_text = (
        f"🎉 <b>Авторизация и настройка завершены!</b>\n\n"
        f"👤 Профиль: <b>{first_name}</b>\n"
        f"📢 {scan_msg}\n\n"
        f"Теперь вы можете настроить тексты сообщений в разделе «✉️ Пул сообщений» и запустить циклическую рассылку."
    )
    await status_msg.edit_text(final_text, reply_markup=get_back_to_main_kb(), parse_mode="HTML")

@router.callback_query(F.data == "auth_logout")
async def cb_auth_logout(callback: CallbackQuery):
    """
    Выход из аккаунта (удаление StringSession и остановка рассылки).
    """
    user_id = callback.from_user.id

    # Останавливаем рассылку если активна
    await broadcaster.stop_broadcast(user_id, reason="User logged out")

    async with get_session() as session:
        await session.execute(delete(UserSession).where(UserSession.user_id == user_id))
        await session.execute(delete(TargetChannel).where(TargetChannel.user_id == user_id))

    await callback.message.edit_text(
        "🚪 <b>Вы успешно вышли из аккаунта.</b>\n\n"
        "Сессия и список каналов удалены из базы данных.",
        reply_markup=get_back_to_main_kb(),
        parse_mode="HTML"
    )
    await callback.answer()
