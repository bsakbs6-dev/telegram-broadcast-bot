from aiogram.fsm.state import State, StatesGroup

class AuthStates(StatesGroup):
    """
    Состояния FSM процесса авторизации пользователя через MTProto.
    """
    waiting_for_phone = State()      # Ожидание ввода номера телефона (+XXXXXXXXXXX)
    waiting_for_code = State()       # Ожидание ввода кода подтверждения (xx.xxx / xx xx x)
    waiting_for_2fa = State()        # Ожидание ввода пароля двухфакторной аутентификации
