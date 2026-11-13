# Файл: bot/dispatcher.py
# Назначение: сборка диспетчера aiogram: подключение обработчиков и разделение
# их между преподавателем и студентами. Общая часть обоих способов запуска.

from __future__ import annotations

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode

from bot.config import Config
from bot.dialogs import DatabaseStorage
from bot.handlers import registration, student, teacher

# Бот реагирует только на сообщения и нажатия кнопок, остальные события Telegram не присылает.
ALLOWED_UPDATES = ["message", "callback_query"]


def build_dispatcher(config: Config) -> Dispatcher:
    """Собирает диспетчер. Вызывать один раз на процесс: роутеры подключаются однократно."""
    dispatcher = Dispatcher(storage=DatabaseStorage())
    dispatcher["config"] = config
    # Бот работает только в личных чатах: в группах сдачи увидели бы все участники.
    dispatcher.message.filter(F.chat.type == ChatType.PRIVATE)

    is_teacher = F.from_user.id == config.teacher_id
    teacher.router.message.filter(is_teacher)
    teacher.router.callback_query.filter(is_teacher)
    for router in (registration.router, student.router):
        router.message.filter(~is_teacher)
        router.callback_query.filter(~is_teacher)

    dispatcher.include_routers(teacher.router, registration.router, student.router)
    return dispatcher


def make_bot(config: Config) -> Bot:
    return Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
