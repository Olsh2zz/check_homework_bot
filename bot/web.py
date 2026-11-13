# Файл: bot/web.py
# Назначение: запуск на Vercel. Telegram сам присылает каждое сообщение на адрес
# /api/webhook, Vercel раз в сутки вызывает /api/cron для напоминания,
# а /api/setup один раз создаёт таблицы и сообщает Telegram адрес вебхука.

from __future__ import annotations

import hmac
import logging
import os
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any, AsyncIterator

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from bot.config import Config, ConfigError, load_config
from bot.db import Database
from bot.dispatcher import ALLOWED_UPDATES, build_dispatcher, make_bot
from bot.drivers import make_driver
from bot.reminders import send_reminder

log = logging.getLogger(__name__)

WEBHOOK_PATH = "/api/webhook"
# В этом заголовке Telegram возвращает секрет, который бот указал при установке вебхука.
TELEGRAM_SECRET_HEADER = "x-telegram-bot-api-secret-token"

HTTP_FORBIDDEN = 403
HTTP_SERVER_ERROR = 500

# Страницы с описанием API отключены: адреса бота посторонним знать незачем.
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def check_web_config(config: Config) -> None:
    """Проверяет настройки, без которых бот не может работать на Vercel."""
    if not config.database_url:
        raise ConfigError("Не задана переменная DATABASE_URL (строка подключения к базе Postgres)")
    if not config.webhook_secret:
        raise ConfigError("Не задана переменная WEBHOOK_SECRET")


@lru_cache(maxsize=1)
def get_config() -> Config:
    config = load_config(os.environ)
    check_web_config(config)
    return config


@lru_cache(maxsize=1)
def get_dispatcher() -> Dispatcher:
    return build_dispatcher(get_config())


def secrets_match(given: str | None, expected: str | None) -> bool:
    """Сравнивает секреты за время, не зависящее от места первого различия."""
    if not given or not expected:
        return False
    return hmac.compare_digest(given.encode(), expected.encode())


@asynccontextmanager
async def open_session(config: Config) -> AsyncIterator[tuple[Bot, Database]]:
    """Бот и соединение с базой на время одного запроса.

    Между запросами Vercel может заморозить программу, и открытые соединения
    за это время обрываются, поэтому они не хранятся дольше запроса.
    """
    db = Database(make_driver(config.db_path, config.database_url))
    await db.connect(create_schema=False)
    bot = make_bot(config)
    try:
        yield bot, db
    finally:
        await bot.session.close()
        await db.close()


async def process_update(
    dispatcher: Dispatcher, bot: Bot, db: Database, payload: dict[str, Any]
) -> bool:
    """Передаёт одно событие Telegram обработчикам. Возвращает False при ошибке.

    Ошибка не пробрасывается дальше: на ответ с ошибкой Telegram присылал бы
    то же событие снова и снова.
    """
    dispatcher.storage.bind(db)
    try:
        update = Update.model_validate(payload, context={"bot": bot})
        await dispatcher.feed_update(bot, update, db=db)
    except Exception:
        log.exception("Не удалось обработать событие Telegram")
        return False
    return True


def _config_or_error() -> Config | JSONResponse:
    try:
        return get_config()
    except ConfigError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=HTTP_SERVER_ERROR)


def _forbidden() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "Неверный секрет"}, status_code=HTTP_FORBIDDEN)


@app.get("/api/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.post(WEBHOOK_PATH)
async def webhook(request: Request) -> Any:
    config = _config_or_error()
    if isinstance(config, JSONResponse):
        return config
    if not secrets_match(request.headers.get(TELEGRAM_SECRET_HEADER), config.webhook_secret):
        return _forbidden()
    payload = await request.json()
    async with open_session(config) as (bot, db):
        await process_update(get_dispatcher(), bot, db, payload)
    return {"ok": True}


@app.get("/api/cron")
async def cron(request: Request) -> Any:
    config = _config_or_error()
    if isinstance(config, JSONResponse):
        return config
    # Vercel подписывает вызовы по расписанию заголовком «Authorization: Bearer <CRON_SECRET>».
    expected = f"Bearer {config.cron_secret}" if config.cron_secret else None
    if not secrets_match(request.headers.get("authorization"), expected):
        return _forbidden()
    async with open_session(config) as (bot, db):
        sent = await send_reminder(bot, db, config)
    return {"ok": True, "sent": sent}


@app.get("/api/setup")
async def setup(request: Request, secret: str = "") -> Any:
    config = _config_or_error()
    if isinstance(config, JSONResponse):
        return config
    if not secrets_match(secret, config.webhook_secret):
        return _forbidden()
    url = f"https://{request.headers['host']}{WEBHOOK_PATH}"
    async with open_session(config) as (bot, db):
        await db.create_schema()
        await bot.set_webhook(
            url, secret_token=config.webhook_secret, allowed_updates=ALLOWED_UPDATES
        )
    return {"ok": True, "webhook": url}
