# Файл: bot/dialogs.py
# Назначение: хранение шагов диалогов в базе данных. На Vercel между сообщениями
# программа не сохраняет память, поэтому шаг диалога нельзя держать в ней.

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Mapping

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey

from bot.db import Database


def dialog_key(chat_id: int, user_id: int) -> str:
    return f"{chat_id}:{user_id}"


class DatabaseStorage(BaseStorage):
    """Хранилище состояний aiogram поверх таблицы dialogs.

    База привязывается вызовом bind(). Привязка действует в текущей задаче
    asyncio и в задачах, созданных из неё, поэтому у каждого запроса на Vercel
    своё соединение с базой.
    """

    def __init__(self) -> None:
        self._current: ContextVar[Database] = ContextVar("dialog_database")

    def bind(self, db: Database) -> None:
        self._current.set(db)

    @property
    def _db(self) -> Database:
        try:
            return self._current.get()
        except LookupError:
            raise RuntimeError("База для диалогов не привязана: вызовите bind()") from None

    @staticmethod
    def _key(key: StorageKey) -> str:
        return dialog_key(key.chat_id, key.user_id)

    async def set_state(self, key: StorageKey, state: str | State | None = None) -> None:
        name = state.state if isinstance(state, State) else state
        await self._db.set_dialog_state(self._key(key), name)

    async def get_state(self, key: StorageKey) -> str | None:
        state, _ = await self._db.get_dialog(self._key(key))
        return state

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        await self._db.set_dialog_data(self._key(key), dict(data))

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        _, data = await self._db.get_dialog(self._key(key))
        return data

    async def close(self) -> None:
        return None
