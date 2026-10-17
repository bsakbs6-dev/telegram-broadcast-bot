from aiogram.fsm.state import State, StatesGroup

class AnonChatStates(StatesGroup):
    """
    FSM состояния для управления модулем анонимного чата (@AnonRuBot).
    """
    waiting_for_message_text = State()
    waiting_for_trigger_symbol = State()
    waiting_for_skip_timeout = State()
