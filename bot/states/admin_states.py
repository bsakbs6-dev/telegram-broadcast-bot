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

    # Смена никнейма (отображаемого имени) аккаунтов
    waiting_for_bulk_nickname = State()
    waiting_for_single_nickname = State()

    # Присоединение всех аккаунтов к группе/каналу
    waiting_for_join_group_target = State()

    # Массовая отправка (спам) в группу всеми аккаунтами
    waiting_for_spam_group = State()
    waiting_for_spam_text = State()
    waiting_for_spam_count = State()
    waiting_for_spam_delay = State()
    waiting_for_spam_custom_delay = State()
