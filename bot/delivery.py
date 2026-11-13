# Файл: bot/delivery.py
# Назначение: пересылка содержимого сдачи преподавателю и обновление
# карточки работы в его чате.

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import InputMediaDocument, InputMediaPhoto

from bot import keyboards, texts
from bot import db as storage
from bot.config import Config
from bot.db import Database

log = logging.getLogger(__name__)

# Ограничение Telegram: в одном альбоме от 2 до 10 вложений.
ALBUM_LIMIT = 10


def group_items(items: list) -> list[list]:
    """Делит сообщения сдачи на порции для отправки.

    Идущие подряд фото (или подряд документы) собираются в альбомы до 10 штук,
    текст всегда идёт отдельной порцией. Порядок сообщений сохраняется.
    """
    groups: list[list] = []
    for item in items:
        last = groups[-1] if groups else None
        can_join = (
            last is not None
            and item["kind"] != storage.KIND_TEXT
            and last[0]["kind"] == item["kind"]
            and len(last) < ALBUM_LIMIT
        )
        if can_join:
            last.append(item)
        else:
            groups.append([item])
    return groups


async def send_items(bot: Bot, chat_id: int, items: list) -> None:
    """Отправляет содержимое сдачи в чат по сохранённым file_id."""
    for group in group_items(items):
        kind = group[0]["kind"]
        if kind == storage.KIND_TEXT:
            await bot.send_message(chat_id, group[0]["text"])
        elif len(group) == 1:
            item = group[0]
            if kind == storage.KIND_PHOTO:
                await bot.send_photo(chat_id, item["file_id"], caption=item["text"])
            else:
                await bot.send_document(chat_id, item["file_id"], caption=item["text"])
        else:
            media_type = InputMediaPhoto if kind == storage.KIND_PHOTO else InputMediaDocument
            media = [media_type(media=item["file_id"], caption=item["text"]) for item in group]
            await bot.send_media_group(chat_id, media)


async def send_to_teacher(bot: Bot, db: Database, config: Config, submission_id: int) -> bool:
    """Присылает преподавателю содержимое работы и карточку с кнопками оценки.

    Возвращает False, если Telegram не принял сообщения (например, преподаватель
    ещё не запускал бота). Работа при этом остаётся в очереди.
    """
    sub = await db.get_submission(submission_id)
    if sub is None:
        return False
    items = await db.get_items(submission_id)
    previous = await db.previous_comment(sub["root_id"], sub["attempt"])
    try:
        await send_items(bot, config.teacher_id, items)
        card = await bot.send_message(
            config.teacher_id,
            texts.submission_card(sub, previous, config.timezone),
            reply_markup=keyboards.review(submission_id),
        )
    except TelegramAPIError:
        log.exception("Не удалось отправить работу %s преподавателю", submission_id)
        return False
    await db.set_teacher_message(submission_id, card.message_id)
    return True


async def refresh_teacher_card(bot: Bot, db: Database, config: Config, submission_id: int) -> None:
    """Переписывает карточку у преподавателя по текущему статусу и убирает кнопки оценки."""
    sub = await db.get_submission(submission_id)
    if sub is None or sub["teacher_msg_id"] is None:
        return
    previous = await db.previous_comment(sub["root_id"], sub["attempt"])
    try:
        await bot.edit_message_text(
            texts.submission_card(sub, previous, config.timezone),
            chat_id=config.teacher_id,
            message_id=sub["teacher_msg_id"],
            reply_markup=None,
        )
    except TelegramBadRequest:
        # Карточку могли удалить из чата. На решение по работе это не влияет.
        log.warning("Не удалось обновить карточку работы %s", submission_id)
