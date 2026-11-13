# Файл: tests/fake_telegram.py
# Назначение: заглушка Telegram для тестов: записывает запросы бота вместо
# отправки в сеть и собирает входящие сообщения и нажатия кнопок.

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import AnswerCallbackQuery, SendMediaGroup, TelegramMethod
from aiogram.types import CallbackQuery, Chat, Message, Update, User

TEST_TOKEN = "42:TEST"


class FakeSession(BaseSession):
    """Сессия, которая не ходит в сеть: каждый запрос бота попадает в список requests."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod] = []
        self._next_message_id = 1000

    async def close(self) -> None:
        return None

    async def stream_content(self, *args: Any, **kwargs: Any):
        yield b""

    def _message(self, chat_id: int, text: str | None = None) -> Message:
        self._next_message_id += 1
        return Message(
            message_id=self._next_message_id,
            date=datetime.now(timezone.utc),
            chat=Chat(id=chat_id, type="private"),
            text=text,
        )

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None):
        self.requests.append(method)
        if isinstance(method, AnswerCallbackQuery):
            return True
        if isinstance(method, SendMediaGroup):
            return [self._message(method.chat_id) for _ in method.media]
        return self._message(method.chat_id, getattr(method, "text", None))


class FakeTelegram:
    """Бот с заглушкой вместо сети и счётчики для сборки входящих апдейтов."""

    def __init__(self) -> None:
        self.session = FakeSession()
        self.bot = Bot(
            TEST_TOKEN,
            session=self.session,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self._update_id = 0
        self._message_id = 0

    def _user(self, user_id: int) -> User:
        return User(id=user_id, is_bot=False, first_name="Тест", username=f"user{user_id}")

    def message(self, user_id: int, **content: Any) -> Update:
        """Входящее сообщение от пользователя. content: text, photo, document и т. д."""
        self._update_id += 1
        self._message_id += 1
        return Update(
            update_id=self._update_id,
            message=Message(
                message_id=self._message_id,
                date=datetime.now(timezone.utc),
                chat=Chat(id=user_id, type="private"),
                from_user=self._user(user_id),
                **content,
            ),
        )

    def callback(self, user_id: int, data: str) -> Update:
        """Нажатие inline-кнопки с указанными данными."""
        self._update_id += 1
        return Update(
            update_id=self._update_id,
            callback_query=CallbackQuery(
                id=str(self._update_id),
                from_user=self._user(user_id),
                chat_instance="test",
                data=data,
            ),
        )

    def sent_to(self, chat_id: int) -> list[TelegramMethod]:
        """Все запросы бота, адресованные указанному чату."""
        return [
            request
            for request in self.session.requests
            if getattr(request, "chat_id", None) == chat_id
        ]

    def last_text(self, chat_id: int) -> str:
        """Текст последнего сообщения, которое бот отправил или изменил в чате."""
        for request in reversed(self.sent_to(chat_id)):
            text = getattr(request, "text", None)
            if text:
                return text
        return ""

    def clear(self) -> None:
        self.session.requests.clear()
