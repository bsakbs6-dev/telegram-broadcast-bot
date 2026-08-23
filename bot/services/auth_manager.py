import asyncio
import logging
import time
from typing import Dict, Optional, Tuple
from pyrogram import Client
from pyrogram.errors import (
    FloodWait,
    PhoneNumberInvalid,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    SessionPasswordNeeded,
    PasswordHashInvalid,
)

from bot.config import config

logger = logging.getLogger(__name__)

class PendingAuth:
    """
    Класс хранения промежуточного состояния авторизации пользователя.
    """
    def __init__(self, client: Client, phone_number: str, phone_code_hash: str):
        self.client = client
        self.phone_number = phone_number
        self.phone_code_hash = phone_code_hash
        self.created_at = time.time()

class AuthManager:
    """
    Менеджер временных Pyrogram клиентов во время FSM авторизации.
    Обеспечивает изоляцию данных и безопасную очистку ресурсов.
    """
    def __init__(self):
        self._pending_auths: Dict[int, PendingAuth] = {}
        self._lock = asyncio.Lock()

    async def start_auth(self, user_id: int, phone_number: str) -> Tuple[bool, str]:
        """
        Инициализация подключения MTProto и отправка кода подтверждения.
        Возвращает (success: bool, message/error_text: str).
        """
        async with self._lock:
            # Если уже была висящая сессия, закрываем ее
            await self._cleanup_unsafe(user_id)

            client = Client(
                name=f"temp_auth_{user_id}_{int(time.time())}",
                api_id=config.API_ID,
                api_hash=config.API_HASH,
                device_model=config.DEVICE_MODEL,
                app_version=config.APP_VERSION,
                system_version=config.SYSTEM_VERSION,
                in_memory=True,
            )

            try:
                await client.connect()
                sent_code = await client.send_code(phone_number)
                self._pending_auths[user_id] = PendingAuth(
                    client=client,
                    phone_number=phone_number,
                    phone_code_hash=sent_code.phone_code_hash,
                )
                delivery_info = "в приложение Telegram"
                if hasattr(sent_code, "type"):
                    st = str(sent_code.type).lower()
                    if "sms" in st:
                        delivery_info = "по SMS на ваш номер"
                    elif "call" in st:
                        delivery_info = "по телефонному звонку"
                    elif "app" in st:
                        delivery_info = "в приложение Telegram (проверьте смартфон и чат «Служебные уведомления»)"

                logger.info(f"Auth code sent to {phone_number} for user {user_id} via {sent_code.type}")
                return True, delivery_info
            except FloodWait as e:
                logger.warning(f"FloodWait for user {user_id}: wait {e.value}s")
                await client.disconnect()
                return False, f"⚠️ Слишком много попыток. Telegram требует подождать {e.value} секунд."
            except PhoneNumberInvalid:
                logger.warning(f"Invalid phone number '{phone_number}' from user {user_id}")
                await client.disconnect()
                return False, "❌ Неверный формат номера телефона. Проверьте правильность и повторите попытку."
            except Exception as e:
                logger.error(f"Error starting auth for user {user_id}: {e}", exc_info=True)
                try:
                    await client.disconnect()
                except Exception:
                    pass
                return False, f"❌ Ошибка отправки кода: {str(e)}"

    async def resend_code(self, user_id: int) -> Tuple[bool, str]:
        """
        Повторная отправка кода (переключение на SMS, если код в приложение не пришел).
        """
        async with self._lock:
            auth_data = self._pending_auths.get(user_id)
            if not auth_data:
                return False, "Сессия авторизации не найдена. Начните вход заново."

            try:
                sent_code = await auth_data.client.resend_code(
                    auth_data.phone_number,
                    auth_data.phone_code_hash,
                )
                auth_data.phone_code_hash = sent_code.phone_code_hash
                logger.info(f"Resent code to {auth_data.phone_number} via {sent_code.type}")
                return True, "📩 Код отправлен по SMS на ваш номер телефона!"
            except FloodWait as e:
                logger.warning(f"FloodWait on resend_code for user {user_id}: {e.value}s")
                return False, f"⚠️ Слишком часто. Подождите {e.value} сек."
            except Exception as e:
                err_str = str(e)
                logger.error(f"Error resending code for user {user_id}: {err_str}")
                if "SEND_CODE_UNAVAILABLE" in err_str:
                    return False, "⚠️ Отправка SMS недоступна для этого номера. Пожалуйста, откройте приложение Telegram и проверьте чат «Служебные уведомления»."
                return False, f"❌ Ошибка отправки SMS: {err_str}"

    async def submit_code(self, user_id: int, code: str) -> Tuple[str, Optional[str], Optional[object]]:
        """
        Отправка кода подтверждения Telegram.
        Возвращает (status: str, message: Optional[str], me_obj: Optional[object])
        Возможные статусы: 'success', 'need_2fa', 'error'
        """
        async with self._lock:
            auth_data = self._pending_auths.get(user_id)
            if not auth_data:
                return "error", "Сессия авторизации устарела или не найдена. Начните вход заново.", None

            client = auth_data.client
            phone_number = auth_data.phone_number
            phone_code_hash = auth_data.phone_code_hash

            try:
                signed_in_user = await client.sign_in(
                    phone_number=phone_number,
                    phone_code_hash=phone_code_hash,
                    phone_code=code,
                )
                session_string = await client.export_session_string()
                me = await client.get_me()

                # Успешный вход без 2FA
                await client.disconnect()
                del self._pending_auths[user_id]
                logger.info(f"User {user_id} successfully signed in via MTProto as @{me.username or me.id}")
                return "success", session_string, me

            except SessionPasswordNeeded:
                logger.info(f"User {user_id} requires 2FA password")
                return "need_2fa", "Требуется ввод пароля двухфакторной аутентификации (2FA).", None

            except PhoneCodeInvalid:
                logger.warning(f"Invalid phone code entered by user {user_id}")
                return "error", "❌ Неверный код подтверждения. Пожалуйста, проверьте и введите снова.", None

            except PhoneCodeExpired:
                logger.warning(f"Phone code expired for user {user_id}")
                await self._cleanup_unsafe(user_id)
                return "error", "⚠️ Срок действия кода истек. Начните авторизацию заново.", None

            except FloodWait as e:
                logger.warning(f"FloodWait on sign_in for user {user_id}: {e.value}s")
                return "error", f"⚠️ Telegram ограничил запросы (FloodWait). Подождите {e.value} сек.", None

            except Exception as e:
                logger.error(f"Unexpected error on submit_code for user {user_id}: {e}", exc_info=True)
                return "error", f"❌ Произошла ошибка: {str(e)}", None

    async def submit_password(self, user_id: int, password: str) -> Tuple[bool, Optional[str], Optional[object]]:
        """
        Отправка пароля 2FA (двухфакторной аутентификации).
        Возвращает (success: bool, session_string_or_error: str, me_obj: Optional[object])
        """
        async with self._lock:
            auth_data = self._pending_auths.get(user_id)
            if not auth_data:
                return False, "Сессия авторизации устарела или не найдена. Начните вход заново.", None

            client = auth_data.client

            try:
                await client.check_password(password)
                session_string = await client.export_session_string()
                me = await client.get_me()

                await client.disconnect()
                del self._pending_auths[user_id]
                logger.info(f"User {user_id} successfully passed 2FA as @{me.username or me.id}")
                return True, session_string, me

            except PasswordHashInvalid:
                logger.warning(f"Invalid 2FA password for user {user_id}")
                return False, "❌ Неверный облачный пароль 2FA. Попробуйте еще раз:", None

            except FloodWait as e:
                logger.warning(f"FloodWait on 2FA for user {user_id}: {e.value}s")
                return False, f"⚠️ Слишком много попыток ввода пароля. Подождите {e.value} сек.", None

            except Exception as e:
                logger.error(f"Error checking 2FA password for user {user_id}: {e}", exc_info=True)
                return False, f"❌ Ошибка проверки 2FA: {str(e)}", None

    async def cleanup(self, user_id: int):
        """
        Очистить сессию авторизации при отмене или ошибке.
        """
        async with self._lock:
            await self._cleanup_unsafe(user_id)

    async def _cleanup_unsafe(self, user_id: int):
        auth_data = self._pending_auths.pop(user_id, None)
        if auth_data and auth_data.client:
            try:
                if auth_data.client.is_connected:
                    await auth_data.client.disconnect()
            except Exception as e:
                logger.debug(f"Error disconnecting client on cleanup: {e}")

# Синглтон менеджер
auth_manager = AuthManager()
