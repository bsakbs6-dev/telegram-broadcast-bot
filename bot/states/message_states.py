from aiogram.fsm.state import State, StatesGroup

class MessageStates(StatesGroup):
    """
    Состояния FSM управления пулом сообщений для ротации.
    """
    waiting_for_message_text = State()  # Ожидание ввода текста для слота
