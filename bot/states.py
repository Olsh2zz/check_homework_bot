# Файл: bot/states.py
# Назначение: состояния диалогов: регистрация, сбор сдачи, проверка, вопрос и ответ.

from aiogram.fsm.state import State, StatesGroup


class Registration(StatesGroup):
    full_name = State()
    group = State()


class Submit(StatesGroup):
    title = State()
    collecting = State()


class Review(StatesGroup):
    score = State()
    comment = State()


class Ask(StatesGroup):
    text = State()


class Answer(StatesGroup):
    text = State()
