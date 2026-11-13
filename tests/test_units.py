# Файл: tests/test_units.py
# Назначение: проверка настроек, разбиения сдачи на альбомы и расчёта
# времени до напоминания.

import unittest
from datetime import datetime, timezone

from bot import db as storage
from bot.config import ConfigError, load_config
from bot.delivery import ALBUM_LIMIT, group_items
from bot.reminders import seconds_until

SECONDS_IN_HOUR = 3600


def item(kind: str) -> dict:
    return {"kind": kind, "text": None, "file_id": "file"}


class ConfigTest(unittest.TestCase):
    def test_defaults(self):
        config = load_config({"BOT_TOKEN": "42:TEST", "TEACHER_ID": "123"})
        self.assertEqual(config.teacher_id, 123)
        self.assertEqual(config.db_path, "homework.db")
        self.assertEqual((config.reminder_hour, config.reminder_days), (9, 2))
        self.assertEqual(str(config.timezone), "Europe/Moscow")

    def test_custom_values(self):
        config = load_config(
            {
                "BOT_TOKEN": "42:TEST",
                "TEACHER_ID": "123",
                "DB_PATH": "data/bot.db",
                "REMINDER_HOUR": "18",
                "REMINDER_DAYS": "0",
                "TIMEZONE": "Asia/Novosibirsk",
            }
        )
        self.assertEqual(config.db_path, "data/bot.db")
        self.assertEqual((config.reminder_hour, config.reminder_days), (18, 0))
        self.assertEqual(str(config.timezone), "Asia/Novosibirsk")

    def test_errors_name_the_variable(self):
        cases = {
            "BOT_TOKEN": {"TEACHER_ID": "123"},
            "TEACHER_ID": {"BOT_TOKEN": "42:TEST"},
            "REMINDER_HOUR": {"BOT_TOKEN": "42:TEST", "TEACHER_ID": "1", "REMINDER_HOUR": "24"},
            "REMINDER_DAYS": {"BOT_TOKEN": "42:TEST", "TEACHER_ID": "1", "REMINDER_DAYS": "-1"},
            "TIMEZONE": {"BOT_TOKEN": "42:TEST", "TEACHER_ID": "1", "TIMEZONE": "Mars/Base"},
        }
        for variable, env in cases.items():
            with self.subTest(variable=variable), self.assertRaises(ConfigError) as caught:
                load_config(env)
            self.assertIn(variable, str(caught.exception))

    def test_database_url_has_second_name(self):
        base = {"BOT_TOKEN": "42:TEST", "TEACHER_ID": "1"}
        self.assertIsNone(load_config(base).database_url)
        self.assertEqual(load_config({**base, "POSTGRES_URL": "pg://b"}).database_url, "pg://b")
        both = {**base, "DATABASE_URL": "pg://a", "POSTGRES_URL": "pg://b"}
        self.assertEqual(load_config(both).database_url, "pg://a")

    def test_teacher_id_must_be_number(self):
        with self.assertRaises(ConfigError):
            load_config({"BOT_TOKEN": "42:TEST", "TEACHER_ID": "@teacher"})


class GroupItemsTest(unittest.TestCase):
    def kinds(self, items: list) -> list[list[str]]:
        return [[entry["kind"] for entry in group] for group in group_items(items)]

    def test_empty(self):
        self.assertEqual(group_items([]), [])

    def test_texts_are_never_joined(self):
        items = [item(storage.KIND_TEXT), item(storage.KIND_TEXT)]
        self.assertEqual(self.kinds(items), [["text"], ["text"]])

    def test_neighbour_photos_form_album(self):
        items = [item(storage.KIND_PHOTO)] * 3
        self.assertEqual(self.kinds(items), [["photo", "photo", "photo"]])

    def test_photos_and_documents_are_separate(self):
        items = [
            item(storage.KIND_PHOTO),
            item(storage.KIND_DOCUMENT),
            item(storage.KIND_DOCUMENT),
            item(storage.KIND_TEXT),
            item(storage.KIND_PHOTO),
        ]
        self.assertEqual(
            self.kinds(items), [["photo"], ["document", "document"], ["text"], ["photo"]]
        )

    def test_album_is_split_at_limit(self):
        items = [item(storage.KIND_PHOTO)] * (ALBUM_LIMIT + 1)
        self.assertEqual([len(group) for group in group_items(items)], [ALBUM_LIMIT, 1])


class SecondsUntilTest(unittest.TestCase):
    def test_later_today(self):
        now = datetime(2026, 11, 13, 7, 0, tzinfo=timezone.utc)
        self.assertEqual(seconds_until(9, now), 2 * SECONDS_IN_HOUR)

    def test_hour_already_passed(self):
        now = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(seconds_until(9, now), 23 * SECONDS_IN_HOUR)

    def test_exact_hour_waits_full_day(self):
        now = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(seconds_until(9, now), 24 * SECONDS_IN_HOUR)


if __name__ == "__main__":
    unittest.main()
