# Файл: tests/test_web.py
# Назначение: проверка запуска на Vercel: приём событий по вебхуку, защита
# секретами, напоминание по расписанию, первичная настройка и хранение
# шагов диалога в базе.

from __future__ import annotations

import json
import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest import mock

from aiogram.methods import SendMessage, SetWebhook
from aiogram.types import PhotoSize
from starlette.requests import Request

from bot import db as storage
from bot import keyboards, web
from bot.config import ConfigError
from bot.dialogs import dialog_key
from bot.drivers import clean_postgres_url, numbered_placeholders
from bot.states import Registration, Submit
from tests.fake_telegram import FakeTelegram
from tests.support import CONFIG, DISPATCHER, TEACHER, open_test_db

STUDENT = 100
HOST = "homework.example.app"


def make_request(
    method: str, path: str, headers: dict[str, str] | None = None, body: dict | None = None
) -> Request:
    """Собирает входящий HTTP-запрос так, как его увидит приложение."""
    raw_headers = [(b"host", HOST.encode())]
    raw_headers += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": raw_headers,
    }
    payload = json.dumps(body or {}).encode()

    async def receive() -> dict:
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request(scope, receive)


def text_update(update_id: int, user_id: int, text: str) -> dict:
    """Событие в том виде, в каком его присылает Telegram: обычный JSON."""
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 1794555600,
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": "Тест"},
            "text": text,
        },
    }


class HelpersTest(unittest.TestCase):
    def test_secrets_match(self):
        self.assertTrue(web.secrets_match("abc", "abc"))
        self.assertFalse(web.secrets_match("abc", "abd"))
        self.assertFalse(web.secrets_match(None, "abc"))
        self.assertFalse(web.secrets_match("", ""))
        # Если секрет не настроен, доступ закрыт для любого значения.
        self.assertFalse(web.secrets_match("abc", None))

    def test_web_config_requires_database_and_secret(self):
        web.check_web_config(CONFIG)
        for field, variable in [("database_url", "DATABASE_URL"), ("webhook_secret", "WEBHOOK_SECRET")]:
            with self.subTest(field=field), self.assertRaises(ConfigError) as caught:
                web.check_web_config(replace(CONFIG, **{field: None}))
            self.assertIn(variable, str(caught.exception))

    def test_numbered_placeholders(self):
        self.assertEqual(
            numbered_placeholders("SELECT * FROM t WHERE a = ? AND b = ?"),
            "SELECT * FROM t WHERE a = $1 AND b = $2",
        )
        self.assertEqual(numbered_placeholders("SELECT 1"), "SELECT 1")

    def test_unsupported_url_params_are_removed(self):
        base = "postgresql://user:pass@ep-test-pooler.eu-central-1.aws.neon.tech/neondb"
        self.assertEqual(
            clean_postgres_url(base + "?sslmode=require&channel_binding=require"),
            base + "?sslmode=require",
        )
        self.assertEqual(clean_postgres_url(base + "?channel_binding=require"), base)
        self.assertEqual(clean_postgres_url(base), base)


class WebTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await open_test_db()
        self.tg = FakeTelegram()
        self.sessions = 0

        @asynccontextmanager
        async def fake_session(config):
            self.sessions += 1
            yield self.tg.bot, self.db

        patches = [
            mock.patch.object(web, "get_config", return_value=CONFIG),
            mock.patch.object(web, "get_dispatcher", return_value=DISPATCHER),
            mock.patch.object(web, "open_session", fake_session),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    async def asyncTearDown(self):
        await self.db.close()

    async def post_update(self, payload: dict, secret: str | None = CONFIG.webhook_secret):
        headers = {web.TELEGRAM_SECRET_HEADER: secret} if secret else {}
        return await web.webhook(make_request("POST", web.WEBHOOK_PATH, headers, payload))

    # ---------- Вебхук ----------

    async def test_webhook_handles_update_from_telegram(self):
        result = await self.post_update(text_update(1, STUDENT, "/start"))
        self.assertEqual(result, {"ok": True})
        self.assertIn("зарегистрируйтесь", self.tg.last_text(STUDENT))

    async def test_webhook_rejects_wrong_or_missing_secret(self):
        for secret in ["wrong", None]:
            with self.subTest(secret=secret):
                response = await self.post_update(text_update(1, STUDENT, "/start"), secret)
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.tg.session.requests, [])
        self.assertEqual(self.sessions, 0)

    async def test_dialog_continues_across_separate_requests(self):
        # Каждое сообщение приходит отдельным запросом: шаг диалога берётся из базы.
        await self.post_update(text_update(1, STUDENT, "/start"))
        state, _ = await self.db.get_dialog(dialog_key(STUDENT, STUDENT))
        self.assertEqual(state, Registration.full_name.state)

        await self.post_update(text_update(2, STUDENT, "Иванов Иван"))
        state, data = await self.db.get_dialog(dialog_key(STUDENT, STUDENT))
        self.assertEqual(state, Registration.group.state)
        self.assertEqual(data, {"full_name": "Иванов Иван"})

        await self.post_update(text_update(3, STUDENT, "ИВТ-21"))
        self.assertEqual(await self.db.get_dialog(dialog_key(STUDENT, STUDENT)), (None, {}))
        self.assertEqual((await self.db.get_student(STUDENT))["group_name"], "ИВТ-21")

    async def test_full_submission_over_webhook(self):
        texts = ["/start", "Иванов Иван", "ИВТ-21", keyboards.BTN_SUBMIT, "Лаб. 1", "Решение"]
        for number, text in enumerate(texts, start=1):
            await self.post_update(text_update(number, STUDENT, text))
        state, _ = await self.db.get_dialog(dialog_key(STUDENT, STUDENT))
        self.assertEqual(state, Submit.collecting.state)

        await self.post_update(text_update(10, STUDENT, keyboards.BTN_SEND))
        queue = await self.db.queue()
        self.assertEqual([sub["title"] for sub in queue], ["Лаб. 1"])
        self.assertIn("Лаб. 1", self.tg.last_text(TEACHER))

    async def test_handler_error_is_not_returned_to_telegram(self):
        with mock.patch.object(DISPATCHER, "feed_update", side_effect=RuntimeError("сбой")):
            with self.assertLogs(web.log, level="ERROR"):
                result = await self.post_update(text_update(1, STUDENT, "/start"))
        self.assertEqual(result, {"ok": True})

    async def test_broken_payload_is_not_returned_to_telegram(self):
        with self.assertLogs(web.log, level="ERROR"):
            result = await self.post_update({"update_id": "не число"})
        self.assertEqual(result, {"ok": True})

    async def test_album_is_confirmed_once(self):
        for number, text in enumerate(
            ["/start", "Иванов Иван", "ИВТ-21", keyboards.BTN_SUBMIT, "Лаб. 1"], start=1
        ):
            await self.post_update(text_update(number, STUDENT, text))
        self.tg.clear()
        size = [PhotoSize(file_id="p", file_unique_id="p", width=1, height=1)]
        for _ in range(3):
            update = self.tg.message(STUDENT, photo=size, media_group_id="album-1")
            await web.process_update(DISPATCHER, self.tg.bot, self.db, update.model_dump())

        replies = [r.text for r in self.tg.sent_to(STUDENT) if isinstance(r, SendMessage)]
        self.assertEqual(replies, ["Альбом добавлен."])
        draft = await self.db.get_draft(STUDENT)
        self.assertEqual(await self.db.count_items(draft["id"]), 3)

    # ---------- Напоминание по расписанию ----------

    async def test_cron_requires_bearer_secret(self):
        for header in [{}, {"Authorization": "Bearer wrong"}, {"Authorization": CONFIG.cron_secret}]:
            with self.subTest(header=header):
                response = await web.cron(make_request("GET", "/api/cron", header))
                self.assertEqual(response.status_code, 403)

    async def test_cron_is_closed_when_secret_is_not_set(self):
        config = replace(CONFIG, cron_secret=None)
        with mock.patch.object(web, "get_config", return_value=config):
            response = await web.cron(
                make_request("GET", "/api/cron", {"Authorization": "Bearer None"})
            )
        self.assertEqual(response.status_code, 403)

    async def test_cron_sends_reminder_about_stale_work(self):
        headers = {"Authorization": f"Bearer {CONFIG.cron_secret}"}
        result = await web.cron(make_request("GET", "/api/cron", headers))
        self.assertEqual(result, {"ok": True, "sent": False})

        await self.db.save_student(STUDENT, None, "Иванов Иван", "ИВТ-21")
        sid = await self.db.create_draft(STUDENT, "Давняя работа")
        await self.db.add_item(sid, 1, storage.KIND_TEXT, "решение", None)
        await self.db.submit_draft(sid, datetime.now(timezone.utc) - timedelta(days=3))

        result = await web.cron(make_request("GET", "/api/cron", headers))
        self.assertEqual(result, {"ok": True, "sent": True})
        self.assertIn("дольше 2 дн.: 1", self.tg.last_text(TEACHER))

    # ---------- Первичная настройка ----------

    async def test_setup_registers_webhook(self):
        result = await web.setup(make_request("GET", "/api/setup"), CONFIG.webhook_secret)
        url = f"https://{HOST}/api/webhook"
        self.assertEqual(result, {"ok": True, "webhook": url})

        call = [r for r in self.tg.session.requests if isinstance(r, SetWebhook)][0]
        self.assertEqual(call.url, url)
        self.assertEqual(call.secret_token, CONFIG.webhook_secret)
        self.assertEqual(call.allowed_updates, ["message", "callback_query"])

    async def test_setup_rejects_wrong_secret(self):
        response = await web.setup(make_request("GET", "/api/setup"), "wrong")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.tg.session.requests, [])

    async def test_missing_settings_are_reported(self):
        with mock.patch.object(web, "get_config", side_effect=ConfigError("Не задана переменная DATABASE_URL")):
            response = await web.setup(make_request("GET", "/api/setup"), "any")
        self.assertEqual(response.status_code, 500)
        self.assertIn("DATABASE_URL", json.loads(response.body)["error"])


if __name__ == "__main__":
    unittest.main()
