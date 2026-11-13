# Файл: tests/test_scenarios.py
# Назначение: сквозные сценарии бота на заглушке Telegram: регистрация, сдача,
# оценка, доработка с пересдачей, отзыв, вопрос и ответ, очередь, напоминания.

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageText,
    SendDocument,
    SendMediaGroup,
    SendMessage,
    SendPhoto,
)
from aiogram.types import Document, PhotoSize

from bot import db as storage
from bot import keyboards
from bot.dialogs import dialog_key
from bot.reminders import send_reminder
from tests.fake_telegram import FakeTelegram
from tests.support import CONFIG, DISPATCHER, TEACHER, open_test_db

STUDENT = 100
STRANGER = 300


def photo(file_id: str) -> list[PhotoSize]:
    return [PhotoSize(file_id=file_id, file_unique_id=file_id, width=100, height=100)]


class ScenarioTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await open_test_db()
        DISPATCHER["db"] = self.db
        DISPATCHER.storage.bind(self.db)
        self.tg = FakeTelegram()

    async def asyncTearDown(self):
        await self.db.close()

    # ---------- Вспомогательные действия ----------

    async def say(self, user_id: int, text: str) -> None:
        await DISPATCHER.feed_update(self.tg.bot, self.tg.message(user_id, text=text))

    async def send(self, user_id: int, **content) -> None:
        await DISPATCHER.feed_update(self.tg.bot, self.tg.message(user_id, **content))

    async def press(self, user_id: int, data: str) -> None:
        await DISPATCHER.feed_update(self.tg.bot, self.tg.callback(user_id, data))

    def alert(self) -> str | None:
        """Текст последнего всплывающего ответа на нажатие кнопки."""
        answers = [r for r in self.tg.session.requests if isinstance(r, AnswerCallbackQuery)]
        return answers[-1].text if answers else None

    async def register(self, user_id: int = STUDENT, name: str = "Иванов Иван") -> None:
        await self.say(user_id, "/start")
        await self.say(user_id, name)
        await self.say(user_id, "ИВТ-21")

    async def submit(self, user_id: int = STUDENT, title: str = "Лаб. 1") -> int:
        """Сдаёт работу из одного текстового сообщения и возвращает её id."""
        await self.say(user_id, keyboards.BTN_SUBMIT)
        await self.say(user_id, title)
        await self.say(user_id, "Моё решение")
        await self.say(user_id, keyboards.BTN_SEND)
        return (await self.db.queue())[-1]["id"]

    async def accept(self, sid: int, score: str = "7/10") -> None:
        await self.press(TEACHER, keyboards.ReviewCb(action="accept", sid=sid).pack())
        await self.say(TEACHER, score)
        await self.say(TEACHER, keyboards.BTN_NO_COMMENT)

    async def reject(self, sid: int, comment: str = "Нет вывода") -> None:
        await self.press(TEACHER, keyboards.ReviewCb(action="reject", sid=sid).pack())
        await self.say(TEACHER, comment)
        await self.say(TEACHER, keyboards.BTN_NO_SCORE)

    # ---------- Регистрация ----------

    async def test_registration_saves_name_and_group(self):
        await self.say(STUDENT, "/start")
        self.assertIn("зарегистрируйтесь", self.tg.last_text(STUDENT))
        await self.say(STUDENT, "Иванов")
        self.assertIn("фамилия и имя", self.tg.last_text(STUDENT))
        await self.say(STUDENT, "  Иванов   Иван ")
        await self.say(STUDENT, "ИВТ-21")

        student = await self.db.get_student(STUDENT)
        self.assertEqual(student["full_name"], "Иванов Иван")
        self.assertEqual(student["group_name"], "ИВТ-21")
        self.assertEqual(student["username"], f"user{STUDENT}")

    async def test_unregistered_user_is_sent_to_registration(self):
        await self.say(STRANGER, keyboards.BTN_SUBMIT)
        self.assertIn("Сначала нужно зарегистрироваться", self.tg.last_text(STRANGER))
        await self.say(STRANGER, "Сидоров Сидор")
        await self.say(STRANGER, "ИВТ-22")
        self.assertIsNotNone(await self.db.get_student(STRANGER))

    async def test_profile_can_be_changed(self):
        await self.register()
        await self.say(STUDENT, keyboards.BTN_PROFILE)
        self.assertIn("Иванов Иван", self.tg.last_text(STUDENT))
        await self.press(STUDENT, keyboards.ProfileCb(action="edit").pack())
        await self.say(STUDENT, "Иванов Иван Иванович")
        await self.say(STUDENT, "ИВТ-31")
        student = await self.db.get_student(STUDENT)
        self.assertEqual(student["full_name"], "Иванов Иван Иванович")
        self.assertEqual(student["group_name"], "ИВТ-31")

    # ---------- Сдача ----------

    async def test_submission_reaches_teacher_with_all_content(self):
        await self.register()
        await self.say(STUDENT, keyboards.BTN_SUBMIT)
        await self.say(STUDENT, "Лаб. 3")
        await self.say(STUDENT, "Ссылка: https://github.com/example/lab3")
        await self.send(STUDENT, photo=photo("photo-1"), caption="Страница 1")
        await self.send(STUDENT, photo=photo("photo-2"))
        await self.send(
            STUDENT, document=Document(file_id="doc-1", file_unique_id="doc-1")
        )
        await self.say(STUDENT, keyboards.BTN_SEND)

        to_teacher = self.tg.sent_to(TEACHER)
        self.assertEqual(
            [type(request) for request in to_teacher],
            [SendMessage, SendMediaGroup, SendDocument, SendMessage],
        )
        self.assertIn("github.com/example/lab3", to_teacher[0].text)
        self.assertEqual([m.media for m in to_teacher[1].media], ["photo-1", "photo-2"])
        self.assertEqual(to_teacher[1].media[0].caption, "Страница 1")
        self.assertEqual(to_teacher[2].document, "doc-1")

        card = to_teacher[3]
        self.assertIn("Лаб. 3", card.text)
        self.assertIn("Иванов Иван (ИВТ-21)", card.text)
        self.assertIn("Попытка: 1", card.text)
        self.assertIn("Ждёт проверки", card.text)
        self.assertEqual(len(card.reply_markup.inline_keyboard[0]), 2)
        self.assertIn("отправлена на проверку", self.tg.last_text(STUDENT))

    async def test_single_photo_is_sent_without_album(self):
        await self.register()
        await self.say(STUDENT, keyboards.BTN_SUBMIT)
        await self.say(STUDENT, "Лаб. 1")
        await self.send(STUDENT, photo=photo("photo-1"))
        await self.say(STUDENT, keyboards.BTN_SEND)
        self.assertIsInstance(self.tg.sent_to(TEACHER)[0], SendPhoto)

    async def test_empty_submission_is_not_sent(self):
        await self.register()
        await self.say(STUDENT, keyboards.BTN_SUBMIT)
        await self.say(STUDENT, "Лаб. 1")
        await self.say(STUDENT, keyboards.BTN_SEND)
        self.assertIn("пока ничего нет", self.tg.last_text(STUDENT))
        self.assertEqual(self.tg.sent_to(TEACHER), [])
        self.assertEqual(await self.db.queue(), [])

    async def test_cancel_drops_the_draft(self):
        await self.register()
        await self.say(STUDENT, keyboards.BTN_SUBMIT)
        await self.say(STUDENT, "Лаб. 1")
        await self.say(STUDENT, "Решение")
        await self.say(STUDENT, keyboards.BTN_CANCEL)
        self.assertIsNone(await self.db.get_draft(STUDENT))
        self.assertEqual(self.tg.sent_to(TEACHER), [])

    async def test_unsupported_message_type_is_refused(self):
        await self.register()
        await self.say(STUDENT, keyboards.BTN_SUBMIT)
        await self.say(STUDENT, "Лаб. 1")
        await self.send(STUDENT, sticker={
            "file_id": "s", "file_unique_id": "s", "type": "regular", "width": 1,
            "height": 1, "is_animated": False, "is_video": False,
        })
        self.assertIn("не принимается", self.tg.last_text(STUDENT))
        draft = await self.db.get_draft(STUDENT)
        self.assertEqual(await self.db.count_items(draft["id"]), 0)

    async def test_draft_survives_lost_dialog_state(self):
        await self.register()
        await self.say(STUDENT, keyboards.BTN_SUBMIT)
        await self.say(STUDENT, "Лаб. 1")
        await self.say(STUDENT, "Часть 1")
        # Шаг диалога потерян, но черновик в базе остался.
        await self.db.set_dialog_state(dialog_key(STUDENT, STUDENT), None)
        await self.say(STUDENT, "Часть 2")
        await self.say(STUDENT, keyboards.BTN_SEND)

        queue = await self.db.queue()
        self.assertEqual(len(queue), 1)
        self.assertEqual(await self.db.count_items(queue[0]["id"]), 2)

    # ---------- Оценка ----------

    async def test_accept_sends_score_to_student(self):
        await self.register()
        sid = await self.submit()
        self.tg.clear()
        await self.press(TEACHER, keyboards.ReviewCb(action="accept", sid=sid).pack())
        await self.say(TEACHER, "7,5/10")
        await self.say(TEACHER, "Аккуратная работа")

        result = self.tg.last_text(STUDENT)
        self.assertIn("✅ Работа «Лаб. 1» принята", result)
        self.assertIn("Баллы: 7.5/10", result)
        self.assertIn("Комментарий: Аккуратная работа", result)
        self.assertIsNone(self.tg.sent_to(STUDENT)[-1].reply_markup)

        edits = [r for r in self.tg.sent_to(TEACHER) if isinstance(r, EditMessageText)]
        self.assertEqual(len(edits), 1)
        self.assertIn("✅ Принято: 7.5/10", edits[0].text)
        self.assertIsNone(edits[0].reply_markup)

    async def test_wrong_score_is_asked_again(self):
        await self.register()
        sid = await self.submit()
        await self.press(TEACHER, keyboards.ReviewCb(action="accept", sid=sid).pack())
        for wrong in ["отлично", "11/10", "5"]:
            await self.say(TEACHER, wrong)
            self.assertIn("Введите ещё раз", self.tg.last_text(TEACHER))
        sub = await self.db.get_submission(sid)
        self.assertEqual(sub["status"], storage.STATUS_PENDING)

        await self.say(TEACHER, "10/10")
        await self.say(TEACHER, keyboards.BTN_NO_COMMENT)
        sub = await self.db.get_submission(sid)
        self.assertEqual((sub["status"], sub["score"]), (storage.STATUS_ACCEPTED, 10))

    async def test_reject_requires_comment_and_offers_fix(self):
        await self.register()
        sid = await self.submit()
        self.tg.clear()
        await self.press(TEACHER, keyboards.ReviewCb(action="reject", sid=sid).pack())
        await self.say(TEACHER, "Нет вывода <в конце>")
        await self.say(TEACHER, "4/10")

        message = self.tg.sent_to(STUDENT)[-1]
        self.assertIn("❌ Работу «Лаб. 1» нужно доработать", message.text)
        self.assertIn("Баллы: 4/10", message.text)
        # Угловые скобки из комментария не должны ломать HTML-разметку сообщения.
        self.assertIn("Нет вывода &lt;в конце&gt;", message.text)
        buttons = [button.text for button in message.reply_markup.inline_keyboard[0]]
        self.assertEqual(buttons, ["🔁 Исправить", "❓ Уточнить"])

    async def test_student_cannot_review(self):
        await self.register()
        sid = await self.submit()
        await self.press(STUDENT, keyboards.ReviewCb(action="accept", sid=sid).pack())
        await self.say(STUDENT, "10/10")
        sub = await self.db.get_submission(sid)
        self.assertEqual(sub["status"], storage.STATUS_PENDING)

    async def test_teacher_cancel_keeps_work_in_queue(self):
        await self.register()
        sid = await self.submit()
        await self.press(TEACHER, keyboards.ReviewCb(action="accept", sid=sid).pack())
        await self.say(TEACHER, keyboards.BTN_CANCEL)
        await self.say(TEACHER, "7/10")
        sub = await self.db.get_submission(sid)
        self.assertEqual(sub["status"], storage.STATUS_PENDING)

    # ---------- Доработка и пересдача ----------

    async def test_resubmission_shows_attempt_and_previous_comment(self):
        await self.register()
        first = await self.submit()
        await self.reject(first, "Нет вывода")
        self.tg.clear()

        await self.press(STUDENT, keyboards.WorkCb(action="fix", sid=first).pack())
        self.assertIn("Пересдача работы «Лаб. 1»", self.tg.last_text(STUDENT))
        await self.say(STUDENT, "Исправленное решение")
        await self.say(STUDENT, keyboards.BTN_SEND)

        card = self.tg.sent_to(TEACHER)[-1].text
        self.assertIn("Попытка: 2", card)
        self.assertIn("Прошлый комментарий: Нет вывода", card)

        second = (await self.db.queue())[0]["id"]
        await self.accept(second, "9/10")
        works = await self.db.list_works(STUDENT)
        self.assertEqual([(w["id"], w["status"]) for w in works], [(second, "accepted")])

    async def test_fix_is_refused_when_newer_attempt_exists(self):
        await self.register()
        first = await self.submit()
        await self.reject(first)
        await self.press(STUDENT, keyboards.WorkCb(action="fix", sid=first).pack())
        await self.say(STUDENT, "Исправленное решение")
        await self.say(STUDENT, keyboards.BTN_SEND)

        await self.press(STUDENT, keyboards.WorkCb(action="fix", sid=first).pack())
        self.assertIn("пересдать нельзя", self.alert())
        self.assertIsNone(await self.db.get_draft(STUDENT))

    # ---------- Мои работы и отзыв ----------

    async def test_my_works_shows_statuses_and_total(self):
        await self.register()
        first = await self.submit(title="Лаб. 1")
        second = await self.submit(title="Лаб. 2")
        third = await self.submit(title="Лаб. 3")
        await self.submit(title="Лаб. 4")
        await self.accept(first, "7/10")
        await self.accept(second, "4.5/5")
        await self.reject(third)
        await self.say(STUDENT, keyboards.BTN_MY)

        message = self.tg.sent_to(STUDENT)[-1]
        self.assertIn("✅ Лаб. 1: 7/10", message.text)
        self.assertIn("✅ Лаб. 2: 4.5/5", message.text)
        self.assertIn("❌ Лаб. 3", message.text)
        self.assertIn("⏳ Лаб. 4", message.text)
        self.assertIn("Сумма баллов за принятые работы: 11.5/15", message.text)
        buttons = [row[0].text for row in message.reply_markup.inline_keyboard]
        self.assertEqual(buttons, ["↩️ Отозвать «Лаб. 4»", "🔁 Исправить «Лаб. 3»"])

    async def test_withdraw_updates_teacher_card_and_blocks_review(self):
        await self.register()
        sid = await self.submit()
        self.tg.clear()
        await self.press(STUDENT, keyboards.WorkCb(action="withdraw", sid=sid).pack())

        edit = [r for r in self.tg.sent_to(TEACHER) if isinstance(r, EditMessageText)][0]
        self.assertIn("Отозвана студентом", edit.text)
        self.assertIsNone(edit.reply_markup)
        self.assertEqual(await self.db.queue(), [])

        await self.press(TEACHER, keyboards.ReviewCb(action="accept", sid=sid).pack())
        self.assertIn("уже проверена или отозвана", self.alert())

    async def test_withdraw_during_review_does_not_save_score(self):
        await self.register()
        sid = await self.submit()
        await self.press(TEACHER, keyboards.ReviewCb(action="accept", sid=sid).pack())
        await self.press(STUDENT, keyboards.WorkCb(action="withdraw", sid=sid).pack())
        await self.say(TEACHER, "7/10")
        await self.say(TEACHER, keyboards.BTN_NO_COMMENT)

        self.assertIn("уже проверена или отозвана", self.tg.last_text(TEACHER))
        sub = await self.db.get_submission(sid)
        self.assertEqual((sub["status"], sub["score"]), (storage.STATUS_WITHDRAWN, None))

    async def test_checked_work_cannot_be_withdrawn(self):
        await self.register()
        sid = await self.submit()
        await self.accept(sid)
        await self.press(STUDENT, keyboards.WorkCb(action="withdraw", sid=sid).pack())
        self.assertIn("нельзя отозвать", self.alert())
        self.assertEqual((await self.db.get_submission(sid))["status"], "accepted")

    # ---------- Вопрос по комментарию ----------

    async def test_question_goes_to_teacher_and_answer_comes_back(self):
        await self.register()
        sid = await self.submit()
        await self.reject(sid, "Нет вывода")
        self.tg.clear()

        await self.press(STUDENT, keyboards.WorkCb(action="ask", sid=sid).pack())
        await self.say(STUDENT, "Какой именно вывод нужен?")
        question = self.tg.sent_to(TEACHER)[-1]
        self.assertIn("Вопрос по работе «Лаб. 1»", question.text)
        self.assertIn("Ваш комментарий: Нет вывода", question.text)
        self.assertIn("Вопрос: Какой именно вывод нужен?", question.text)

        await self.press(TEACHER, question.reply_markup.inline_keyboard[0][0].callback_data)
        await self.say(TEACHER, "Сравнение с теорией")
        answer = self.tg.last_text(STUDENT)
        self.assertIn("Ваш вопрос: Какой именно вывод нужен?", answer)
        self.assertIn("Ответ: Сравнение с теорией", answer)

    async def test_question_about_foreign_work_is_refused(self):
        await self.register()
        await self.register(STRANGER, "Сидоров Сидор")
        sid = await self.submit()
        await self.reject(sid)
        await self.press(STRANGER, keyboards.WorkCb(action="ask", sid=sid).pack())
        self.assertIn("не найдена", self.alert())

    # ---------- Очередь и напоминания ----------

    async def test_queue_lists_pending_and_reopens_work(self):
        await self.register()
        await self.register(STRANGER, "Сидоров Сидор")
        first = await self.submit(STUDENT, "Лаб. 1")
        second = await self.submit(STRANGER, "Лаб. 2")
        await self.accept(first)
        self.tg.clear()

        await self.say(TEACHER, keyboards.BTN_QUEUE)
        listing = self.tg.sent_to(TEACHER)[-1]
        self.assertIn("Ждут проверки: 1", listing.text)
        self.assertIn("Сидоров Сидор (ИВТ-21): Лаб. 2, сегодня", listing.text)

        self.tg.clear()
        await self.press(TEACHER, listing.reply_markup.inline_keyboard[0][0].callback_data)
        reopened = self.tg.sent_to(TEACHER)
        self.assertEqual(reopened[0].text, "Моё решение")
        self.assertIn("Лаб. 2", reopened[1].text)
        self.assertEqual(
            reopened[1].reply_markup.inline_keyboard[0][0].callback_data,
            keyboards.ReviewCb(action="accept", sid=second).pack(),
        )

    async def test_empty_queue(self):
        await self.say(TEACHER, keyboards.BTN_QUEUE)
        self.assertIn("Очередь пуста", self.tg.last_text(TEACHER))

    async def test_reminder_only_for_stale_works(self):
        await self.register()
        self.assertFalse(await send_reminder(self.tg.bot, self.db, CONFIG))

        sid = await self.db.create_draft(STUDENT, "Давняя работа")
        await self.db.add_item(sid, 1, storage.KIND_TEXT, "решение", None)
        await self.db.submit_draft(sid, datetime.now(timezone.utc) - timedelta(days=3))
        self.tg.clear()

        self.assertTrue(await send_reminder(self.tg.bot, self.db, CONFIG))
        reminder = self.tg.sent_to(TEACHER)[-1]
        self.assertIn("дольше 2 дн.: 1", reminder.text)
        self.tg.clear()
        await self.press(TEACHER, reminder.reply_markup.inline_keyboard[0][0].callback_data)
        self.assertIn("Ждут проверки: 1", self.tg.last_text(TEACHER))


if __name__ == "__main__":
    unittest.main()
