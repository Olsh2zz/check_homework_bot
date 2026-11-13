# Файл: bot/db.py
# Назначение: схема базы и все запросы бота: студенты, сдачи с цепочкой пересдач,
# содержимое сдач, вопросы по комментариям и шаги диалогов.

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from bot.drivers import Driver, SqliteDriver

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

# Схема общая для SQLite и Postgres. Различается только описание столбца id,
# его подставляет драйвер. Время хранится текстом в UTC (ISO 8601): такие строки
# сравниваются как даты и одинаково работают в обоих движках.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    tg_id      BIGINT PRIMARY KEY,
    username   TEXT,
    full_name  TEXT NOT NULL,
    group_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS submissions (
    id             {primary_key},
    student_id     BIGINT NOT NULL REFERENCES students(tg_id),
    title          TEXT NOT NULL,
    status         TEXT NOT NULL,
    attempt        INTEGER NOT NULL DEFAULT 1,
    root_id        BIGINT,
    score          DOUBLE PRECISION,
    max_score      DOUBLE PRECISION,
    comment        TEXT,
    teacher_msg_id BIGINT,
    created_at     TEXT NOT NULL,
    reviewed_at    TEXT
);

CREATE TABLE IF NOT EXISTS submission_items (
    id            {primary_key},
    submission_id BIGINT NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    position      BIGINT NOT NULL,
    kind          TEXT NOT NULL,
    text          TEXT,
    file_id       TEXT,
    album         TEXT
);

CREATE TABLE IF NOT EXISTS questions (
    id            {primary_key},
    submission_id BIGINT NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    text          TEXT NOT NULL,
    answer        TEXT,
    created_at    TEXT NOT NULL,
    answered_at   TEXT
);

CREATE TABLE IF NOT EXISTS dialogs (
    key   TEXT PRIMARY KEY,
    state TEXT,
    data  TEXT NOT NULL DEFAULT '{{}}'
);

CREATE INDEX IF NOT EXISTS idx_submissions_student ON submissions(student_id);
CREATE INDEX IF NOT EXISTS idx_submissions_status ON submissions(status);
CREATE INDEX IF NOT EXISTS idx_items_submission ON submission_items(submission_id);
"""

EMPTY_DATA = "{}"

# Запрос сдачи вместе с данными студента: карточке преподавателя нужны оба набора полей.
_SUBMISSION_SELECT = """
SELECT s.*, st.full_name, st.group_name, st.username
FROM submissions s JOIN students st ON st.tg_id = s.student_id
"""


def _iso(moment: datetime | None) -> str:
    return (moment or datetime.now(timezone.utc)).isoformat(timespec="seconds")


class Database:
    """Все запросы бота поверх драйвера SQLite или Postgres."""

    def __init__(self, driver: Driver | str) -> None:
        # Строка означает путь к файлу SQLite: так базу открывают тесты и локальный запуск.
        self._driver = SqliteDriver(driver) if isinstance(driver, str) else driver

    async def connect(self, create_schema: bool = True) -> None:
        """Открывает соединение. Таблицы создаются, если их ещё нет."""
        await self._driver.connect()
        if create_schema:
            await self.create_schema()

    async def create_schema(self) -> None:
        schema = _SCHEMA.format(primary_key=self._driver.primary_key)
        await self._driver.script(schema + self._driver.schema_suffix)

    async def close(self) -> None:
        await self._driver.close()

    async def _one(self, sql: str, params: tuple = ()) -> Any | None:
        return await self._driver.fetch_one(sql, params)

    async def _all(self, sql: str, params: tuple = ()) -> list:
        return await self._driver.fetch_all(sql, params)

    async def _execute(self, sql: str, params: tuple = ()) -> int:
        return await self._driver.execute(sql, params)

    # ---------- Студенты ----------

    async def save_student(
        self, tg_id: int, username: str | None, full_name: str, group_name: str
    ) -> None:
        """Регистрирует студента или обновляет его ФИО и группу."""
        await self._execute(
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

    async def get_student(self, tg_id: int) -> Any | None:
        return await self._one("SELECT * FROM students WHERE tg_id = ?", (tg_id,))

    # ---------- Черновик и отправка ----------

    async def create_draft(
        self, student_id: int, title: str, parent_id: int | None = None
    ) -> int:
        """Создаёт черновик сдачи и возвращает его id. Прежний черновик студента удаляется.

        Если указан parent_id, черновик становится следующей попыткой той же работы.
        """
        async with self._driver.transaction() as tx:
            await tx.execute(
                "DELETE FROM submissions WHERE student_id = ? AND status = ?",
                (student_id, STATUS_DRAFT),
            )
            root_id: int | None = None
            attempt = 1
            if parent_id is not None:
                parent = await tx.fetch_one(
                    "SELECT root_id FROM submissions WHERE id = ?", (parent_id,)
                )
                if parent is None:
                    raise ValueError(f"Сдача {parent_id} не найдена")
                root_id = parent["root_id"]
                last = await tx.fetch_one(
                    "SELECT MAX(attempt) AS last FROM submissions WHERE root_id = ?",
                    (root_id,),
                )
                attempt = last["last"] + 1
            created = await tx.fetch_one(
                """
                INSERT INTO submissions (student_id, title, status, attempt, root_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (student_id, title, STATUS_DRAFT, attempt, root_id, _iso(None)),
            )
            draft_id = created["id"]
            if root_id is None:
                # Первая попытка сама является корнем цепочки пересдач.
                await tx.execute(
                    "UPDATE submissions SET root_id = id WHERE id = ?", (draft_id,)
                )
            return draft_id

    async def get_draft(self, student_id: int) -> Any | None:
        return await self._one(
            "SELECT * FROM submissions WHERE student_id = ? AND status = ?",
            (student_id, STATUS_DRAFT),
        )

    async def delete_draft(self, student_id: int) -> None:
        await self._execute(
            "DELETE FROM submissions WHERE student_id = ? AND status = ?",
            (student_id, STATUS_DRAFT),
        )

    async def add_item(
        self,
        submission_id: int,
        position: int,
        kind: str,
        text: str | None,
        file_id: str | None,
        album: str | None = None,
    ) -> int:
        """Добавляет в сдачу сообщение и возвращает число сообщений в ней.

        position задаёт порядок показа: сюда передаётся id сообщения в Telegram.
        album: идентификатор альбома, если сообщение пришло в составе альбома.
        """
        await self._execute(
            """
            INSERT INTO submission_items (submission_id, position, kind, text, file_id, album)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (submission_id, position, kind, text, file_id, album),
        )
        return await self.count_items(submission_id)

    async def album_seen(self, submission_id: int, album: str) -> bool:
        """Есть ли в сдаче хотя бы одно сообщение из этого альбома."""
        row = await self._one(
            "SELECT 1 AS found FROM submission_items WHERE submission_id = ? AND album = ? LIMIT 1",
            (submission_id, album),
        )
        return row is not None

    async def count_items(self, submission_id: int) -> int:
        row = await self._one(
            "SELECT COUNT(*) AS n FROM submission_items WHERE submission_id = ?",
            (submission_id,),
        )
        return row["n"]

    async def get_items(self, submission_id: int) -> list:
        return await self._all(
            "SELECT * FROM submission_items WHERE submission_id = ? ORDER BY position, id",
            (submission_id,),
        )

    async def submit_draft(self, submission_id: int, now: datetime | None = None) -> bool:
        """Переводит черновик в очередь проверки. Возвращает False, если черновика нет."""
        changed = await self._execute(
            "UPDATE submissions SET status = ?, created_at = ? WHERE id = ? AND status = ?",
            (STATUS_PENDING, _iso(now), submission_id, STATUS_DRAFT),
        )
        return changed == 1

    # ---------- Проверка ----------

    async def get_submission(self, submission_id: int) -> Any | None:
        return await self._one(_SUBMISSION_SELECT + "WHERE s.id = ?", (submission_id,))

    async def set_teacher_message(self, submission_id: int, message_id: int) -> None:
        await self._execute(
            "UPDATE submissions SET teacher_msg_id = ? WHERE id = ?",
            (message_id, submission_id),
        )

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
        changed = await self._execute(
            """
            UPDATE submissions
            SET status = ?, score = ?, max_score = ?, comment = ?, reviewed_at = ?
            WHERE id = ? AND status = ?
            """,
            (status, score, max_score, comment, _iso(None), submission_id, STATUS_PENDING),
        )
        return changed == 1

    async def withdraw(self, submission_id: int, student_id: int) -> bool:
        """Отзывает непроверенную сдачу студента. Возвращает False, если отзывать уже нечего."""
        changed = await self._execute(
            "UPDATE submissions SET status = ? WHERE id = ? AND student_id = ? AND status = ?",
            (STATUS_WITHDRAWN, submission_id, student_id, STATUS_PENDING),
        )
        return changed == 1

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

    async def queue(self) -> list:
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

    async def list_works(self, student_id: int) -> list:
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
        row = await self._one(
            """
            INSERT INTO questions (submission_id, text, created_at) VALUES (?, ?, ?)
            RETURNING id
            """,
            (submission_id, text, _iso(None)),
        )
        return row["id"]

    async def get_question(self, question_id: int) -> Any | None:
        return await self._one("SELECT * FROM questions WHERE id = ?", (question_id,))

    async def answer_question(self, question_id: int, answer: str) -> None:
        await self._execute(
            "UPDATE questions SET answer = ?, answered_at = ? WHERE id = ?",
            (answer, _iso(None), question_id),
        )

    # ---------- Шаги диалогов ----------

    async def get_dialog(self, key: str) -> tuple[str | None, dict[str, Any]]:
        """Шаг диалога и его данные. Для незнакомого ключа возвращает (None, {})."""
        row = await self._one("SELECT state, data FROM dialogs WHERE key = ?", (key,))
        if row is None:
            return None, {}
        return row["state"], json.loads(row["data"])

    async def set_dialog_state(self, key: str, state: str | None) -> None:
        await self._execute(
            """
            INSERT INTO dialogs (key, state) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET state = excluded.state
            """,
            (key, state),
        )
        if state is None:
            await self._drop_empty_dialog(key)

    async def set_dialog_data(self, key: str, data: dict[str, Any]) -> None:
        await self._execute(
            """
            INSERT INTO dialogs (key, data) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET data = excluded.data
            """,
            (key, json.dumps(data, ensure_ascii=False)),
        )
        if not data:
            await self._drop_empty_dialog(key)

    async def _drop_empty_dialog(self, key: str) -> None:
        # Завершённый диалог не хранится: в таблице остаются только незаконченные.
        await self._execute(
            "DELETE FROM dialogs WHERE key = ? AND state IS NULL AND data = ?",
            (key, EMPTY_DATA),
        )
