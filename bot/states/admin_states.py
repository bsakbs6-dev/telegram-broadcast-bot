from aiogram.fsm.state import State, StatesGroup

class AdminStates(StatesGroup):
    """
    Состояния FSM панели администратора.
    """
    waiting_for_whitelist_user = State()  # Ожидание ввода user_id или @username для добавления в Whitelist
    waiting_for_del_whitelist_user = State()  # Ожидание ввода user_id или @username для удаления из Whitelist
