# Файл: bot/__main__.py
# Назначение: запуск на своём компьютере или сервере: бот сам опрашивает Telegram
# и работает, пока запущена программа. Для Vercel используется bot/web.py.

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from bot.config import Config, ConfigError, load_config
from bot.db import Database
from bot.dispatcher import ALLOWED_UPDATES, build_dispatcher, make_bot
from bot.drivers import make_driver
from bot.reminders import reminder_loop


async def run(config: Config) -> None:
    db = Database(make_driver(config.db_path, config.database_url))
    await db.connect()
    bot = make_bot(config)
    dispatcher = build_dispatcher(config)
    dispatcher["db"] = db
    dispatcher.storage.bind(db)
    reminders = asyncio.create_task(reminder_loop(bot, db, config))
    try:
        webhook = await bot.get_webhook_info()
        if webhook.url:
            # Telegram не отдаёт сообщения опросом, пока у бота включён вебхук. Отключать его
            # молча нельзя: этим же токеном может пользоваться бот, запущенный на Vercel.
            print(
                f"У этого бота включён вебхук ({webhook.url}), поэтому локальный запуск "
                "невозможен. Создайте для проверки отдельного бота у @BotFather.",
                file=sys.stderr,
            )
            return
        await dispatcher.start_polling(bot, allowed_updates=ALLOWED_UPDATES)
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
