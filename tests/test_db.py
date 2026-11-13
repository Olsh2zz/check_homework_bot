# Файл: tests/test_db.py
# Назначение: проверка запросов к базе: регистрация, черновик и отправка,
# оценка, цепочка пересдач, отзыв, очередь, напоминания и вопросы.

import unittest
from datetime import datetime, timedelta, timezone

from bot import db as storage
from tests.support import open_test_db

STUDENT = 100
OTHER_STUDENT = 200


class DatabaseTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await open_test_db()
        await self.db.save_student(STUDENT, "ivanov", "Иванов Иван", "ИВТ-21")
        await self.db.save_student(OTHER_STUDENT, None, "Петров Пётр", "ИВТ-22")

    async def asyncTearDown(self):
        await self.db.close()

    async def _submit(self, title="Лаб. 1", parent_id=None, student=STUDENT, now=None) -> int:
        """Создаёт сдачу с одним текстовым сообщением и отправляет её на проверку."""
        sid = await self.db.create_draft(student, title, parent_id)
        await self.db.add_item(sid, 1, storage.KIND_TEXT, "решение", None)
        await self.db.submit_draft(sid, now)
        return sid

    async def test_student_is_saved_and_updated(self):
        await self.db.save_student(STUDENT, "ivanov", "Иванов Иван Иванович", "ИВТ-31")
        student = await self.db.get_student(STUDENT)
        self.assertEqual(student["full_name"], "Иванов Иван Иванович")
        self.assertEqual(student["group_name"], "ИВТ-31")
        self.assertIsNone(await self.db.get_student(999))

    async def test_new_draft_replaces_old_one(self):
        first = await self.db.create_draft(STUDENT, "Первая")
        await self.db.add_item(first, 1, storage.KIND_TEXT, "текст", None)
        second = await self.db.create_draft(STUDENT, "Вторая")
        draft = await self.db.get_draft(STUDENT)
        self.assertEqual(draft["id"], second)
        self.assertEqual(await self.db.count_items(first), 0)

    async def test_items_keep_message_order(self):
        sid = await self.db.create_draft(STUDENT, "Лаб. 1")
        await self.db.add_item(sid, 12, storage.KIND_PHOTO, None, "photo-2")
        count = await self.db.add_item(sid, 11, storage.KIND_PHOTO, "подпись", "photo-1")
        self.assertEqual(count, 2)
        items = await self.db.get_items(sid)
        self.assertEqual([item["file_id"] for item in items], ["photo-1", "photo-2"])

    async def test_submit_moves_draft_to_queue(self):
        sid = await self._submit()
        self.assertIsNone(await self.db.get_draft(STUDENT))
        sub = await self.db.get_submission(sid)
        self.assertEqual(sub["status"], storage.STATUS_PENDING)
        self.assertEqual(sub["attempt"], 1)
        self.assertEqual(sub["root_id"], sid)
        self.assertEqual(sub["full_name"], "Иванов Иван")
        self.assertFalse(await self.db.submit_draft(sid))

    async def test_accept_saves_score_and_comment(self):
        sid = await self._submit()
        self.assertTrue(await self.db.review(sid, True, 7.5, 10, "хорошо"))
        sub = await self.db.get_submission(sid)
        self.assertEqual(sub["status"], storage.STATUS_ACCEPTED)
        self.assertEqual((sub["score"], sub["max_score"], sub["comment"]), (7.5, 10, "хорошо"))

    async def test_review_is_applied_only_once(self):
        sid = await self._submit()
        self.assertTrue(await self.db.review(sid, False, None, None, "нет вывода"))
        self.assertFalse(await self.db.review(sid, True, 10, 10, None))
        sub = await self.db.get_submission(sid)
        self.assertEqual(sub["status"], storage.STATUS_REJECTED)

    async def test_resubmission_continues_the_chain(self):
        first = await self._submit()
        await self.db.review(first, False, 4, 10, "нет вывода")
        self.assertTrue(await self.db.can_resubmit(first, STUDENT))

        second = await self._submit(parent_id=first)
        sub = await self.db.get_submission(second)
        self.assertEqual(sub["attempt"], 2)
        self.assertEqual(sub["root_id"], first)
        self.assertEqual(await self.db.previous_comment(first, 2), "нет вывода")
        self.assertIsNone(await self.db.previous_comment(first, 1))
        # Пока вторая попытка на проверке, первую пересдать ещё раз нельзя.
        self.assertFalse(await self.db.can_resubmit(first, STUDENT))

    async def test_resubmit_is_only_for_own_rejected_work(self):
        sid = await self._submit()
        self.assertFalse(await self.db.can_resubmit(sid, STUDENT))
        await self.db.review(sid, False, None, None, "переделать")
        self.assertFalse(await self.db.can_resubmit(sid, OTHER_STUDENT))
        self.assertTrue(await self.db.can_resubmit(sid, STUDENT))

    async def test_withdraw_only_pending_and_only_own(self):
        sid = await self._submit()
        self.assertFalse(await self.db.withdraw(sid, OTHER_STUDENT))
        self.assertTrue(await self.db.withdraw(sid, STUDENT))
        self.assertFalse(await self.db.withdraw(sid, STUDENT))
        self.assertFalse(await self.db.review(sid, True, 10, 10, None))

    async def test_withdrawn_retry_returns_previous_attempt(self):
        first = await self._submit()
        await self.db.review(first, False, None, None, "переделать")
        second = await self._submit(parent_id=first)
        await self.db.withdraw(second, STUDENT)

        works = await self.db.list_works(STUDENT)
        self.assertEqual([work["id"] for work in works], [first])
        self.assertTrue(await self.db.can_resubmit(first, STUDENT))
        third = await self._submit(parent_id=first)
        self.assertEqual((await self.db.get_submission(third))["attempt"], 3)

    async def test_works_show_latest_attempt_of_each_work(self):
        first = await self._submit("Лаб. 1")
        await self.db.review(first, False, None, None, "переделать")
        second = await self._submit("Лаб. 1", parent_id=first)
        other = await self._submit("Лаб. 2")
        await self._submit("Чужая", student=OTHER_STUDENT)
        await self.db.create_draft(STUDENT, "Черновик")

        works = await self.db.list_works(STUDENT)
        self.assertEqual({work["id"] for work in works}, {second, other})

    async def test_queue_is_ordered_from_oldest(self):
        now = datetime.now(timezone.utc)
        new = await self._submit("Новая", now=now)
        old = await self._submit("Старая", student=OTHER_STUDENT, now=now - timedelta(days=3))
        done = await self._submit("Проверенная", now=now - timedelta(days=5))
        await self.db.review(done, True, 5, 5, None)

        queue = await self.db.queue()
        self.assertEqual([sub["id"] for sub in queue], [old, new])

    async def test_stale_counts_only_old_pending(self):
        now = datetime.now(timezone.utc)
        await self._submit("Свежая", now=now - timedelta(hours=5))
        await self._submit("Давняя", student=OTHER_STUDENT, now=now - timedelta(days=3))
        done = await self._submit("Проверенная", now=now - timedelta(days=9))
        await self.db.review(done, True, 5, 5, None)

        self.assertEqual(await self.db.count_stale(2, now), 1)
        self.assertEqual(await self.db.count_stale(0, now), 2)
        self.assertEqual(await self.db.count_stale(7, now), 0)

    async def test_question_and_answer(self):
        sid = await self._submit()
        qid = await self.db.create_question(sid, "Что не так с выводом?")
        question = await self.db.get_question(qid)
        self.assertEqual(question["submission_id"], sid)
        self.assertIsNone(question["answer"])

        await self.db.answer_question(qid, "Нет единиц измерения")
        self.assertEqual((await self.db.get_question(qid))["answer"], "Нет единиц измерения")


if __name__ == "__main__":
    unittest.main()
