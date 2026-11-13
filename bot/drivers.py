# Файл: bot/drivers.py
# Назначение: два способа хранить данные с одинаковым интерфейсом: файл SQLite
# для запуска на своём компьютере и Postgres (Neon) для запуска на Vercel.

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any, AsyncIterator, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import aiosqlite

CONNECT_TIMEOUT_SECONDS = 10

# Параметры строки подключения, которые понимают другие клиенты Postgres, но не asyncpg.
# Он передал бы их серверу как настройки, и сервер отказал бы в соединении.
# Такие параметры есть в строках, которые выдают Neon и Vercel.
UNSUPPORTED_URL_PARAMS = {"channel_binding", "connect_timeout", "gssencmode", "pgbouncer"}


def clean_postgres_url(url: str) -> str:
    """Убирает из строки подключения параметры, которые asyncpg не поддерживает."""
    parts = urlsplit(url)
    query = [
        (name, value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
        if name not in UNSUPPORTED_URL_PARAMS
    ]
    return urlunsplit(parts._replace(query=urlencode(query)))


@lru_cache(maxsize=None)
def numbered_placeholders(sql: str) -> str:
    """Заменяет «?» на «$1», «$2» и так далее: такой вид параметров ждёт Postgres."""
    parts = sql.split("?")
    numbered = [f"{part}${index}" for index, part in enumerate(parts[:-1], start=1)]
    return "".join(numbered) + parts[-1]


class Transaction:
    """Запросы внутри открытой транзакции. Объект выдаёт Driver.transaction()."""

    def __init__(self, driver: Driver) -> None:
        self._driver = driver

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list:
        return await self._driver._fetch(sql, params)

    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Any | None:
        rows = await self._driver._fetch(sql, params)
        return rows[0] if rows else None

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        return await self._driver._run(sql, params)


class Driver:
    """Общая часть драйверов. Запросы пишутся с параметрами «?».

    Соединение одно, а сообщения могут обрабатываться параллельно, поэтому
    каждый запрос и каждая транзакция выполняются под замком.
    """

    # Описание столбца id, который база заполняет сама: синтаксис у движков разный.
    primary_key = ""
    # Команды, которые нужны только этому движку и выполняются после создания таблиц.
    schema_suffix = ""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError

    async def script(self, sql: str) -> None:
        """Выполняет несколько команд без параметров, разделённых «;»."""
        raise NotImplementedError

    async def _fetch(self, sql: str, params: Sequence[Any]) -> list:
        raise NotImplementedError

    async def _run(self, sql: str, params: Sequence[Any]) -> int:
        raise NotImplementedError

    async def _commit(self) -> None:
        return None

    def _atomic(self):
        raise NotImplementedError

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list:
        async with self._lock:
            rows = await self._fetch(sql, params)
            await self._commit()
            return rows

    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Any | None:
        rows = await self.fetch_all(sql, params)
        return rows[0] if rows else None

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Выполняет изменяющий запрос и возвращает число затронутых строк."""
        async with self._lock:
            count = await self._run(sql, params)
            await self._commit()
            return count

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Transaction]:
        """Несколько запросов, которые применяются вместе или не применяются совсем."""
        async with self._lock:
            async with self._atomic():
                yield Transaction(self)


class SqliteDriver(Driver):
    primary_key = "INTEGER PRIMARY KEY AUTOINCREMENT"

    def __init__(self, path: str) -> None:
        super().__init__()
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    @property
    def _db(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("База не открыта: сначала вызовите connect()")
        return self._conn

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        # Без этой команды SQLite не удаляет содержимое сдачи вместе с самой сдачей.
        await self._conn.execute("PRAGMA foreign_keys = ON")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def script(self, sql: str) -> None:
        await self._db.executescript(sql)
        await self._db.commit()

    async def _fetch(self, sql: str, params: Sequence[Any]) -> list:
        async with self._db.execute(sql, tuple(params)) as cursor:
            return list(await cursor.fetchall())

    async def _run(self, sql: str, params: Sequence[Any]) -> int:
        cursor = await self._db.execute(sql, tuple(params))
        return cursor.rowcount

    async def _commit(self) -> None:
        await self._db.commit()

    @asynccontextmanager
    async def _atomic(self) -> AsyncIterator[None]:
        try:
            yield
        except BaseException:
            await self._db.rollback()
            raise
        await self._db.commit()


class PostgresDriver(Driver):
    primary_key = "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY"
    # Некоторые хостинги Postgres (например, Supabase) открывают таблицы через свой
    # REST API. Защита строк без единого разрешающего правила закрывает этот доступ,
    # а бот подключается владельцем таблиц, на которого защита не действует.
    schema_suffix = """
ALTER TABLE students ENABLE ROW LEVEL SECURITY;
ALTER TABLE submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE submission_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE questions ENABLE ROW LEVEL SECURITY;
ALTER TABLE dialogs ENABLE ROW LEVEL SECURITY;
"""

    def __init__(self, url: str) -> None:
        super().__init__()
        self._url = url
        self._conn = None

    async def connect(self) -> None:
        # Импорт здесь, чтобы для запуска на SQLite библиотека asyncpg была не нужна.
        import asyncpg

        # Пул соединений Neon и Supabase не поддерживает подготовленные запросы,
        # поэтому их кеш в asyncpg отключается.
        self._conn = await asyncpg.connect(
            clean_postgres_url(self._url),
            statement_cache_size=0,
            timeout=CONNECT_TIMEOUT_SECONDS,
        )

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def script(self, sql: str) -> None:
        await self._conn.execute(sql)

    async def _fetch(self, sql: str, params: Sequence[Any]) -> list:
        return await self._conn.fetch(numbered_placeholders(sql), *params)

    async def _run(self, sql: str, params: Sequence[Any]) -> int:
        status = await self._conn.execute(numbered_placeholders(sql), *params)
        # Postgres отвечает строкой вида «UPDATE 1»: последнее слово и есть число строк.
        tail = status.rsplit(" ", 1)[-1]
        return int(tail) if tail.isdigit() else 0

    @asynccontextmanager
    async def _atomic(self) -> AsyncIterator[None]:
        async with self._conn.transaction():
            yield


def make_driver(db_path: str, database_url: str | None) -> Driver:
    """Выбирает движок: Postgres, если задан адрес базы, иначе файл SQLite."""
    if database_url:
        return PostgresDriver(database_url)
    return SqliteDriver(db_path)
