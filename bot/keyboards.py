# Файл: bot/keyboards.py
# Назначение: подписи кнопок, клавиатуры меню и данные inline-кнопок.

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

BTN_SUBMIT = "📤 Сдать работу"
BTN_MY = "📚 Мои работы"
BTN_PROFILE = "👤 Профиль"
BTN_QUEUE = "📥 Очередь проверки"
BTN_SEND = "✅ Отправить"
BTN_CANCEL = "Отмена"
BTN_NO_COMMENT = "Без комментария"
BTN_NO_SCORE = "Без баллов"

ACTION_ACCEPT = "accept"
ACTION_REJECT = "reject"
ACTION_WITHDRAW = "withdraw"
ACTION_FIX = "fix"
ACTION_ASK = "ask"

# Значение sid, при котором кнопка очереди открывает весь список, а не одну работу.
QUEUE_LIST = 0
# Telegram не показывает длинные подписи кнопок целиком, поэтому название обрезается.
TITLE_IN_BUTTON = 28
# Больше кнопок под одним сообщением неудобно листать на телефоне.
MAX_QUEUE_BUTTONS = 20


class ReviewCb(CallbackData, prefix="rev"):
    action: str
    sid: int


class WorkCb(CallbackData, prefix="work"):
    action: str
    sid: int


class QueueCb(CallbackData, prefix="queue"):
    sid: int


class AnswerCb(CallbackData, prefix="ans"):
    qid: int


class ProfileCb(CallbackData, prefix="profile"):
    action: str


def _reply(*rows: list[str]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=text) for text in row] for row in rows],
        resize_keyboard=True,
    )


def _short(title: str) -> str:
    return title if len(title) <= TITLE_IN_BUTTON else title[: TITLE_IN_BUTTON - 1] + "…"


def student_menu() -> ReplyKeyboardMarkup:
    return _reply([BTN_SUBMIT], [BTN_MY, BTN_PROFILE])


def teacher_menu() -> ReplyKeyboardMarkup:
    return _reply([BTN_QUEUE])


def cancel_only() -> ReplyKeyboardMarkup:
    return _reply([BTN_CANCEL])


def collecting() -> ReplyKeyboardMarkup:
    return _reply([BTN_SEND], [BTN_CANCEL])


def optional_comment() -> ReplyKeyboardMarkup:
    return _reply([BTN_NO_COMMENT], [BTN_CANCEL])


def optional_score() -> ReplyKeyboardMarkup:
    return _reply([BTN_NO_SCORE], [BTN_CANCEL])


def review(sid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Принять",
                    callback_data=ReviewCb(action=ACTION_ACCEPT, sid=sid).pack(),
                ),
                InlineKeyboardButton(
                    text="❌ На доработку",
                    callback_data=ReviewCb(action=ACTION_REJECT, sid=sid).pack(),
                ),
            ]
        ]
    )


def rejected_actions(sid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔁 Исправить",
                    callback_data=WorkCb(action=ACTION_FIX, sid=sid).pack(),
                ),
                InlineKeyboardButton(
                    text="❓ Уточнить",
                    callback_data=WorkCb(action=ACTION_ASK, sid=sid).pack(),
                ),
            ]
        ]
    )


def works_actions(pending: list, rejected: list) -> InlineKeyboardMarkup | None:
    """Кнопки под списком работ: отзыв для непроверенных, пересдача для отклонённых."""
    rows = [
        [
            InlineKeyboardButton(
                text=f"↩️ Отозвать «{_short(work['title'])}»",
                callback_data=WorkCb(action=ACTION_WITHDRAW, sid=work["id"]).pack(),
            )
        ]
        for work in pending
    ]
    rows += [
        [
            InlineKeyboardButton(
                text=f"🔁 Исправить «{_short(work['title'])}»",
                callback_data=WorkCb(action=ACTION_FIX, sid=work["id"]).pack(),
            )
        ]
        for work in rejected
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def queue_items(submissions: list) -> InlineKeyboardMarkup | None:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{index}. {_short(sub['full_name'])}: {_short(sub['title'])}",
                callback_data=QueueCb(sid=sub["id"]).pack(),
            )
        ]
        for index, sub in enumerate(submissions[:MAX_QUEUE_BUTTONS], start=1)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def open_queue() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть очередь",
                    callback_data=QueueCb(sid=QUEUE_LIST).pack(),
                )
            ]
        ]
    )


def answer_question(qid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Ответить", callback_data=AnswerCb(qid=qid).pack())]
        ]
    )


def edit_profile() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Изменить", callback_data=ProfileCb(action="edit").pack()
                )
            ]
        ]
    )
