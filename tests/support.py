# Файл: tests/support.py
# Назначение: общие для тестов настройки, диспетчер и открытие чистой базы.
# По умолчанию база SQLite в памяти. Если задана переменная TEST_DATABASE_URL,
# те же тесты идут на Postgres.

from __future__ import annotations

import os
from zoneinfo import ZoneInfo

from bot.config import Config
from bot.db import Database
from bot.dispatcher import build_dispatcher
from bot.drivers import PostgresDriver

TEACHER = 1

CONFIG = Config(
    bot_token="42:TEST",
    teacher_id=TEACHER,
    db_path=":memory:",
    reminder_hour=9,
    reminder_days=2,
    timezone=ZoneInfo("Europe/Moscow"),
    database_url="postgresql://test",
    webhook_secret="webhook-secret",
    cron_secret="cron-secret-0123456789",
)

# Роутеры можно подключить только к одному диспетчеру, поэтому он общий на все тесты,
# а база подменяется перед каждым тестом.
DISPATCHER = build_dispatcher(CONFIG)

_TABLES = "dialogs, questions, submission_items, submissions, students"


async def open_test_db() -> Database:
    """Открывает пустую базу с созданными таблицами."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        db = Database(":memory:")
        await db.connect()
        return db
    driver = PostgresDriver(url)
    db = Database(driver)
    await db.connect(create_schema=False)
    await driver.script(f"DROP TABLE IF EXISTS {_TABLES} CASCADE")
    await db.create_schema()
    return db
