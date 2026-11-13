# Файл: bot/__main__.py
# Назначение: точка запуска: читает настройки, открывает базу, подключает
# обработчики и запускает получение сообщений от Telegram.

from __future__ import annotations

import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

from bot.config import Config, ConfigError, load_config
from bot.db import Database
from bot.handlers import registration, student, teacher
from bot.reminders import reminder_loop


def build_dispatcher(config: Config, db: Database) -> Dispatcher:
    """Собирает диспетчер: делит обработчики между преподавателем и студентами."""
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher["db"] = db
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


async def run(config: Config) -> None:
    db = Database(config.db_path)
    await db.connect()
    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = build_dispatcher(config, db)
    reminders = asyncio.create_task(reminder_loop(bot, db, config))
    try:
        await dispatcher.start_polling(bot)
    finally:
        reminders.cancel()
        await bot.session.close()
        await db.close()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    load_dotenv()
    try:
        config = load_config(os.environ)
    except ConfigError as error:
        print(f"Ошибка настроек: {error}", file=sys.stderr)
        return 1
    asyncio.run(run(config))
    return 0


if __name__ == "__main__":
    sys.exit(main())
