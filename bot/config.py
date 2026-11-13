# Файл: bot/config.py
# Назначение: чтение настроек бота из переменных окружения и их проверка.

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_DB_PATH = "homework.db"
DEFAULT_REMINDER_HOUR = 9
DEFAULT_REMINDER_DAYS = 2
DEFAULT_TIMEZONE = "Europe/Moscow"
LAST_HOUR = 23


class ConfigError(ValueError):
    """Ошибка в настройках: текст объясняет, какую переменную исправить."""


@dataclass(frozen=True)
class Config:
    bot_token: str
    teacher_id: int
    db_path: str
    reminder_hour: int
    reminder_days: int
    timezone: ZoneInfo
    # Три настройки ниже нужны только для запуска на Vercel.
    database_url: str | None = None
    webhook_secret: str | None = None
    cron_secret: str | None = None


def _read_int(env: Mapping[str, str], name: str, default: int | None) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        if default is None:
            raise ConfigError(f"Не задана переменная {name}")
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"Переменная {name} должна быть целым числом, получено: {raw}") from None


def load_config(env: Mapping[str, str]) -> Config:
    """Собирает настройки из словаря окружения. Бросает ConfigError с описанием проблемы."""
    token = env.get("BOT_TOKEN", "").strip()
    if not token:
        raise ConfigError("Не задана переменная BOT_TOKEN (токен выдаёт @BotFather)")

    teacher_id = _read_int(env, "TEACHER_ID", None)
    reminder_hour = _read_int(env, "REMINDER_HOUR", DEFAULT_REMINDER_HOUR)
    if not 0 <= reminder_hour <= LAST_HOUR:
        raise ConfigError("REMINDER_HOUR должен быть в диапазоне от 0 до 23")
    reminder_days = _read_int(env, "REMINDER_DAYS", DEFAULT_REMINDER_DAYS)
    if reminder_days < 0:
        raise ConfigError("REMINDER_DAYS не может быть отрицательным")

    tz_name = env.get("TIMEZONE", "").strip() or DEFAULT_TIMEZONE
    try:
        timezone = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"Неизвестный часовой пояс в TIMEZONE: {tz_name}") from None

    return Config(
        bot_token=token,
        teacher_id=teacher_id,
        db_path=env.get("DB_PATH", "").strip() or DEFAULT_DB_PATH,
        reminder_hour=reminder_hour,
        reminder_days=reminder_days,
        timezone=timezone,
        # POSTGRES_URL: под таким именем строку подключения задают некоторые интеграции Vercel.
        database_url=(
            env.get("DATABASE_URL", "").strip() or env.get("POSTGRES_URL", "").strip() or None
        ),
        webhook_secret=env.get("WEBHOOK_SECRET", "").strip() or None,
        cron_secret=env.get("CRON_SECRET", "").strip() or None,
    )
