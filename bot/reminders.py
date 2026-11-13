# Файл: bot/reminders.py
# Назначение: ежедневное напоминание преподавателю о работах,
# которые ждут проверки дольше заданного числа дней.

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from bot import keyboards, texts
from bot.config import Config
from bot.db import Database

log = logging.getLogger(__name__)


def seconds_until(hour: int, now: datetime) -> float:
    """Сколько секунд осталось до ближайшего наступления часа hour (0..23) после now."""
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def send_reminder(bot: Bot, db: Database, config: Config) -> bool:
    """Отправляет напоминание, если есть залежавшиеся работы. Возвращает, было ли оно отправлено."""
    count = await db.count_stale(config.reminder_days)
    if count == 0:
        return False
    try:
        await bot.send_message(
            config.teacher_id,
            texts.reminder(count, config.reminder_days),
            reply_markup=keyboards.open_queue(),
        )
    except TelegramAPIError:
        log.exception("Не удалось отправить напоминание преподавателю")
        return False
    return True


async def reminder_loop(bot: Bot, db: Database, config: Config) -> None:
    """Бесконечный цикл: ждёт часа напоминания в поясе из настроек и проверяет очередь."""
    while True:
        now = datetime.now(config.timezone)
        await asyncio.sleep(seconds_until(config.reminder_hour, now))
        await send_reminder(bot, db, config)
