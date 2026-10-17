from aiogram.fsm.state import State, StatesGroup

class AdminStates(StatesGroup):
    """
    Состояния FSM панели администратора.
    """
    waiting_for_whitelist_user = State()  # Ожидание ввода user_id или @username для добавления в Whitelist
    waiting_for_del_whitelist_user = State()  # Ожидание ввода user_id или @username для удаления из Whitelist

    # Блокировка пользователя
    waiting_for_ban_user = State()
    waiting_for_ban_reason = State()
    waiting_for_ban_duration = State()

    # Добавление администратора
    waiting_for_new_admin = State()

    # Добавление канала в обязательную подписку
    waiting_for_new_sub_channel = State()
