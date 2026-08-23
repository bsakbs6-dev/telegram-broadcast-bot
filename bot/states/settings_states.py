from aiogram.fsm.state import State, StatesGroup

class SettingsStates(StatesGroup):
    """
    Состояния FSM настройки рассылки.
    """
    waiting_for_custom_interval = State()  # Ожидание ввода кастомного интервала в минутах
    waiting_for_jitter_range = State()     # Ожидание ввода диапазона джиттера
