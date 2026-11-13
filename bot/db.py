# Файл: bot/db.py
# Назначение: схема SQLite и все запросы бота: студенты, сдачи с цепочкой
# пересдач, содержимое сдач и вопросы по комментариям.

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

STATUS_DRAFT = "draft"
STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"
STATUS_WITHDRAWN = "withdrawn"

KIND_TEXT = "text"
KIND_PHOTO = "photo"
KIND_DOCUMENT = "document"

# Статусы, которые студент видит в списке своих работ.
# Черновики и отозванные попытки в список не попадают.
_VISIBLE = f"('{STATUS_PENDING}', '{STATUS_ACCEPTED}', '{STATUS_REJECTED}')"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    tg_id      INTEGER PRIMARY KEY,
    username   TEXT,
    full_name  TEXT NOT NULL,
    group_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS submissions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id     INTEGER NOT NULL REFERENCES students(tg_id),
    title          TEXT NOT NULL,
    status         TEXT NOT NULL,
    attempt        INTEGER NOT NULL DEFAULT 1,
    root_id        INTEGER,
    score          REAL,
    max_score      REAL,
    comment        TEXT,
    teacher_msg_id INTEGER,
    created_at     TEXT NOT NULL,
    reviewed_at    TEXT
);

CREATE TABLE IF NOT EXISTS submission_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    position      INTEGER NOT NULL,
    kind          TEXT NOT NULL,
    text          TEXT,
    file_id       TEXT
);

CREATE TABLE IF NOT EXISTS questions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    text          TEXT NOT NULL,
    answer        TEXT,
    created_at    TEXT NOT NULL,
    answered_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_submissions_student ON submissions(student_id);
CREATE INDEX IF NOT EXISTS idx_submissions_status ON submissions(status);
CREATE INDEX IF NOT EXISTS idx_items_submission ON submission_items(submission_id);
"""

# Запрос сдачи вместе с данными студента: карточке преподавателя нужны оба набора полей.
_SUBMISSION_SELECT = """
SELECT s.*, st.full_name, st.group_name, st.username
FROM submissions s JOIN students st ON st.tg_id = s.student_id
"""


def _iso(moment: datetime | None) -> str:
    return (moment or datetime.now(timezone.utc)).isoformat(timespec="seconds")


class Database:
    """Обёртка над одним соединением SQLite. Перед работой нужно вызвать connect()."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None
        # Апдейты обрабатываются параллельно, а соединение одно: замок не даёт
        # операциям из нескольких запросов перемешаться между собой.
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def _db(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("База не открыта: сначала вызовите connect()")
        return self._conn

    async def _one(self, sql: str, params: tuple = ()) -> aiosqlite.Row | None:
        async with self._db.execute(sql, params) as cursor:
            return await cursor.fetchone()

    async def _all(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        async with self._db.execute(sql, params) as cursor:
            return list(await cursor.fetchall())

    # ---------- Студенты ----------

    async def save_student(
        self, tg_id: int, username: str | None, full_name: str, group_name: str
    ) -> None:
        """Регистрирует студента или обновляет его ФИО и группу."""
        async with self._lock:
            await self._db.execute(
                """
                INSERT INTO students (tg_id, username, full_name, group_name, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(tg_id) DO UPDATE SET
                    username = excluded.username,
                    full_name = excluded.full_name,
                    group_name = excluded.group_name
                """,
                (tg_id, username, full_name, group_name, _iso(None)),
            )
            await self._db.commit()

    async def get_student(self, tg_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM students WHERE tg_id = ?", (tg_id,))

    # ---------- Черновик и отправка ----------

    async def create_draft(
        self, student_id: int, title: str, parent_id: int | None = None
    ) -> int:
        """Создаёт черновик сдачи и возвращает его id. Прежний черновик студента удаляется.

        Если указан parent_id, черновик становится следующей попыткой той же работы.
        """
        async with self._lock:
            await self._db.execute(
                "DELETE FROM submissions WHERE student_id = ? AND status = ?",
                (student_id, STATUS_DRAFT),
            )
            root_id: int | None = None
            attempt = 1
            if parent_id is not None:
                parent = await self._one(
                    "SELECT root_id FROM submissions WHERE id = ?", (parent_id,)
                )
                if parent is None:
                    raise ValueError(f"Сдача {parent_id} не найдена")
                root_id = parent["root_id"]
                last = await self._one(
                    "SELECT MAX(attempt) AS last FROM submissions WHERE root_id = ?",
                    (root_id,),
                )
                attempt = last["last"] + 1
            cursor = await self._db.execute(
                """
                INSERT INTO submissions (student_id, title, status, attempt, root_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (student_id, title, STATUS_DRAFT, attempt, root_id, _iso(None)),
            )
            draft_id = cursor.lastrowid
            if root_id is None:
                # Первая попытка сама является корнем цепочки пересдач.
                await self._db.execute(
                    "UPDATE submissions SET root_id = id WHERE id = ?", (draft_id,)
                )
            await self._db.commit()
            return draft_id

    async def get_draft(self, student_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM submissions WHERE student_id = ? AND status = ?",
            (student_id, STATUS_DRAFT),
        )

    async def delete_draft(self, student_id: int) -> None:
        async with self._lock:
            await self._db.execute(
                "DELETE FROM submissions WHERE student_id = ? AND status = ?",
                (student_id, STATUS_DRAFT),
            )
            await self._db.commit()

    async def add_item(
        self,
        submission_id: int,
        position: int,
        kind: str,
        text: str | None,
        file_id: str | None,
    ) -> int:
        """Добавляет в сдачу сообщение и возвращает число сообщений в ней.

        position задаёт порядок показа: сюда передаётся id сообщения в Telegram.
        """
        async with self._lock:
            await self._db.execute(
                """
                INSERT INTO submission_items (submission_id, position, kind, text, file_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (submission_id, position, kind, text, file_id),
            )
            await self._db.commit()
        return await self.count_items(submission_id)

    async def count_items(self, submission_id: int) -> int:
        row = await self._one(
            "SELECT COUNT(*) AS n FROM submission_items WHERE submission_id = ?",
            (submission_id,),
        )
        return row["n"]

    async def get_items(self, submission_id: int) -> list[aiosqlite.Row]:
        return await self._all(
            "SELECT * FROM submission_items WHERE submission_id = ? ORDER BY position, id",
            (submission_id,),
        )

    async def submit_draft(self, submission_id: int, now: datetime | None = None) -> bool:
        """Переводит черновик в очередь проверки. Возвращает False, если черновика нет."""
        async with self._lock:
            cursor = await self._db.execute(
                "UPDATE submissions SET status = ?, created_at = ? WHERE id = ? AND status = ?",
                (STATUS_PENDING, _iso(now), submission_id, STATUS_DRAFT),
            )
            await self._db.commit()
            return cursor.rowcount == 1

    # ---------- Проверка ----------

    async def get_submission(self, submission_id: int) -> aiosqlite.Row | None:
        return await self._one(_SUBMISSION_SELECT + "WHERE s.id = ?", (submission_id,))

    async def set_teacher_message(self, submission_id: int, message_id: int) -> None:
        async with self._lock:
            await self._db.execute(
                "UPDATE submissions SET teacher_msg_id = ? WHERE id = ?",
                (message_id, submission_id),
            )
            await self._db.commit()

    async def review(
        self,
        submission_id: int,
        accepted: bool,
        score: float | None,
        max_score: float | None,
        comment: str | None,
    ) -> bool:
        """Записывает решение преподавателя. Возвращает False, если работа уже не ждёт проверки."""
        status = STATUS_ACCEPTED if accepted else STATUS_REJECTED
        async with self._lock:
            cursor = await self._db.execute(
                """
                UPDATE submissions
                SET status = ?, score = ?, max_score = ?, comment = ?, reviewed_at = ?
                WHERE id = ? AND status = ?
                """,
                (status, score, max_score, comment, _iso(None), submission_id, STATUS_PENDING),
            )
            await self._db.commit()
            return cursor.rowcount == 1

    async def withdraw(self, submission_id: int, student_id: int) -> bool:
        """Отзывает непроверенную сдачу студента. Возвращает False, если отзывать уже нечего."""
        async with self._lock:
            cursor = await self._db.execute(
                "UPDATE submissions SET status = ? WHERE id = ? AND student_id = ? AND status = ?",
                (STATUS_WITHDRAWN, submission_id, student_id, STATUS_PENDING),
            )
            await self._db.commit()
            return cursor.rowcount == 1

    async def previous_comment(self, root_id: int, attempt: int) -> str | None:
        """Комментарий к последней отклонённой попытке, которая была раньше указанной."""
        row = await self._one(
            """
            SELECT comment FROM submissions
            WHERE root_id = ? AND attempt < ? AND status = ?
            ORDER BY attempt DESC LIMIT 1
            """,
            (root_id, attempt, STATUS_REJECTED),
        )
        return row["comment"] if row else None

    async def queue(self) -> list[aiosqlite.Row]:
        """Работы, ждущие проверки, от самых давних к новым."""
        return await self._all(
            _SUBMISSION_SELECT + "WHERE s.status = ? ORDER BY s.created_at, s.id",
            (STATUS_PENDING,),
        )

    async def count_stale(self, days: int, now: datetime | None = None) -> int:
        """Сколько работ ждёт проверки дольше указанного числа дней."""
        border = (now or datetime.now(timezone.utc)) - timedelta(days=days)
        row = await self._one(
            "SELECT COUNT(*) AS n FROM submissions WHERE status = ? AND created_at <= ?",
            (STATUS_PENDING, _iso(border)),
        )
        return row["n"]

    # ---------- Работы студента ----------

    async def list_works(self, student_id: int) -> list[aiosqlite.Row]:
        """Последняя видимая попытка каждой работы студента, сначала новые."""
        return await self._all(
            f"""
            SELECT s.* FROM submissions s
            WHERE s.student_id = ? AND s.status IN {_VISIBLE}
              AND s.attempt = (
                  SELECT MAX(x.attempt) FROM submissions x
                  WHERE x.root_id = s.root_id AND x.status IN {_VISIBLE}
              )
            ORDER BY s.created_at DESC, s.id DESC
            """,
            (student_id,),
        )

    async def can_resubmit(self, submission_id: int, student_id: int) -> bool:
        """Пересдать можно отклонённую работу, после которой нет более новой попытки."""
        row = await self._one(
            f"""
            SELECT s.id FROM submissions s
            WHERE s.id = ? AND s.student_id = ? AND s.status = ?
              AND s.attempt = (
                  SELECT MAX(x.attempt) FROM submissions x
                  WHERE x.root_id = s.root_id AND x.status IN {_VISIBLE}
              )
            """,
            (submission_id, student_id, STATUS_REJECTED),
        )
        return row is not None

    # ---------- Вопросы по комментарию ----------

    async def create_question(self, submission_id: int, text: str) -> int:
        async with self._lock:
            cursor = await self._db.execute(
                "INSERT INTO questions (submission_id, text, created_at) VALUES (?, ?, ?)",
                (submission_id, text, _iso(None)),
            )
            await self._db.commit()
            return cursor.lastrowid

    async def get_question(self, question_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM questions WHERE id = ?", (question_id,))

    async def answer_question(self, question_id: int, answer: str) -> None:
        async with self._lock:
            await self._db.execute(
                "UPDATE questions SET answer = ?, answered_at = ? WHERE id = ?",
                (answer, _iso(None), question_id),
            )
            await self._db.commit()
