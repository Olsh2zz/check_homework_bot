# Файл: bot/handlers/teacher.py
# Назначение: действия преподавателя: оценка работ, очередь проверки
# и ответы на вопросы студентов.

from __future__ import annotations

from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot import db as storage
from bot import delivery, keyboards, texts
from bot.config import Config
from bot.db import Database
from bot.scoring import ScoreError, parse_score
from bot.states import Answer, Review

router = Router(name="teacher")

MAX_COMMENT_LENGTH = 1500

ASK_SCORE = "Введите баллы в формате «балл/максимум», например 7/10."
GONE = "Эта работа уже проверена или отозвана студентом."


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Здравствуйте! Вы преподаватель: работы студентов будут приходить сюда. "
        f"Все непроверенные работы открывает кнопка «{keyboards.BTN_QUEUE}».",
        reply_markup=keyboards.teacher_menu(),
    )


@router.message(F.text == keyboards.BTN_CANCEL)
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.", reply_markup=keyboards.teacher_menu())


# ---------- Очередь ----------


async def _show_queue(bot: Bot, db: Database, config: Config) -> None:
    submissions = await db.queue()
    await bot.send_message(
        config.teacher_id,
        texts.queue_list(submissions),
        reply_markup=keyboards.queue_items(submissions),
    )


@router.message(F.text == keyboards.BTN_QUEUE)
async def queue(message: Message, state: FSMContext, db: Database, config: Config) -> None:
    await state.clear()
    await _show_queue(message.bot, db, config)


@router.callback_query(keyboards.QueueCb.filter())
async def queue_open(
    callback: CallbackQuery,
    callback_data: keyboards.QueueCb,
    db: Database,
    config: Config,
    bot: Bot,
) -> None:
    if callback_data.sid == keyboards.QUEUE_LIST:
        await _show_queue(bot, db, config)
        await callback.answer()
        return
    sub = await db.get_submission(callback_data.sid)
    if sub is None or sub["status"] != storage.STATUS_PENDING:
        await callback.answer(GONE, show_alert=True)
        return
    await delivery.send_to_teacher(bot, db, config, sub["id"])
    await callback.answer()


# ---------- Оценка ----------


@router.callback_query(keyboards.ReviewCb.filter())
async def review_start(
    callback: CallbackQuery,
    callback_data: keyboards.ReviewCb,
    state: FSMContext,
    db: Database,
    config: Config,
    bot: Bot,
) -> None:
    sub = await db.get_submission(callback_data.sid)
    if sub is None or sub["status"] != storage.STATUS_PENDING:
        await callback.answer(GONE, show_alert=True)
        return
    accepted = callback_data.action == keyboards.ACTION_ACCEPT
    await state.clear()
    await state.update_data(sid=sub["id"], accepted=accepted)
    work = f"«{escape(sub['title'])}», {escape(sub['full_name'])}"
    if accepted:
        # Принятая работа: сначала баллы, потом необязательный комментарий.
        await state.set_state(Review.score)
        await bot.send_message(
            config.teacher_id,
            f"✅ Принять работу {work}.\n{ASK_SCORE}",
            reply_markup=keyboards.cancel_only(),
        )
    else:
        # Доработка: сначала обязательный комментарий, потом необязательные баллы.
        await state.set_state(Review.comment)
        await bot.send_message(
            config.teacher_id,
            f"❌ Вернуть на доработку работу {work}.\nНапишите, что нужно исправить.",
            reply_markup=keyboards.cancel_only(),
        )
    await callback.answer()


@router.message(Review.score, F.text)
async def review_score(
    message: Message, state: FSMContext, db: Database, config: Config, bot: Bot
) -> None:
    data = await state.get_data()
    if not data["accepted"] and message.text == keyboards.BTN_NO_SCORE:
        await _finish_review(message, state, db, config, bot)
        return
    try:
        score, max_score = parse_score(message.text)
    except ScoreError as error:
        await message.answer(f"{error}. Введите ещё раз.")
        return
    await state.update_data(score=score, max_score=max_score)
    if data["accepted"]:
        await state.set_state(Review.comment)
        await message.answer(
            f"Напишите комментарий для студента или нажмите «{keyboards.BTN_NO_COMMENT}».",
            reply_markup=keyboards.optional_comment(),
        )
    else:
        await _finish_review(message, state, db, config, bot)


@router.message(Review.comment, F.text)
async def review_comment(
    message: Message, state: FSMContext, db: Database, config: Config, bot: Bot
) -> None:
    data = await state.get_data()
    if data["accepted"] and message.text == keyboards.BTN_NO_COMMENT:
        await _finish_review(message, state, db, config, bot)
        return
    comment = message.text.strip()
    if len(comment) > MAX_COMMENT_LENGTH:
        await message.answer(
            f"Комментарий должен быть не длиннее {MAX_COMMENT_LENGTH} символов. Сократите его."
        )
        return
    await state.update_data(comment=comment)
    if data["accepted"]:
        await _finish_review(message, state, db, config, bot)
    else:
        await state.set_state(Review.score)
        await message.answer(
            f"{ASK_SCORE} Если баллы не нужны, нажмите «{keyboards.BTN_NO_SCORE}».",
            reply_markup=keyboards.optional_score(),
        )


async def _finish_review(
    message: Message, state: FSMContext, db: Database, config: Config, bot: Bot
) -> None:
    """Сохраняет решение, сообщает результат студенту и обновляет карточку."""
    data = await state.get_data()
    await state.clear()
    saved = await db.review(
        data["sid"],
        data["accepted"],
        data.get("score"),
        data.get("max_score"),
        data.get("comment"),
    )
    if not saved:
        await message.answer(GONE, reply_markup=keyboards.teacher_menu())
        return

    sub = await db.get_submission(data["sid"])
    await delivery.refresh_teacher_card(bot, db, config, sub["id"])
    markup = None if data["accepted"] else keyboards.rejected_actions(sub["id"])
    try:
        await bot.send_message(
            sub["student_id"], texts.result_for_student(sub), reply_markup=markup
        )
    except TelegramAPIError:
        await message.answer(
            "Оценка сохранена, но сообщить студенту не удалось: возможно, он заблокировал "
            "бота. Результат он увидит в списке своих работ.",
            reply_markup=keyboards.teacher_menu(),
        )
        return
    await message.answer(
        "Оценка сохранена, студент получил результат.", reply_markup=keyboards.teacher_menu()
    )


# ---------- Ответ на вопрос ----------


@router.callback_query(keyboards.AnswerCb.filter())
async def answer_start(
    callback: CallbackQuery,
    callback_data: keyboards.AnswerCb,
    state: FSMContext,
    db: Database,
    config: Config,
    bot: Bot,
) -> None:
    question = await db.get_question(callback_data.qid)
    if question is None:
        await callback.answer("Вопрос не найден.", show_alert=True)
        return
    await state.clear()
    await state.set_state(Answer.text)
    await state.update_data(qid=question["id"])
    await bot.send_message(
        config.teacher_id,
        "Напишите ответ студенту одним сообщением.",
        reply_markup=keyboards.cancel_only(),
    )
    await callback.answer()


@router.message(Answer.text, F.text)
async def answer_text(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    answer = message.text.strip()
    if len(answer) > MAX_COMMENT_LENGTH:
        await message.answer(
            f"Ответ должен быть не длиннее {MAX_COMMENT_LENGTH} символов. Сократите его."
        )
        return
    data = await state.get_data()
    await state.clear()
    question = await db.get_question(data["qid"])
    sub = await db.get_submission(question["submission_id"])
    await db.answer_question(question["id"], answer)
    try:
        await bot.send_message(
            sub["student_id"], texts.answer_for_student(sub, question["text"], answer)
        )
    except TelegramAPIError:
        await message.answer(
            "Ответ сохранён, но доставить его студенту не удалось.",
            reply_markup=keyboards.teacher_menu(),
        )
        return
    await message.answer("Ответ отправлен студенту.", reply_markup=keyboards.teacher_menu())


# ---------- Всё остальное ----------


@router.message(Review.score)
@router.message(Review.comment)
@router.message(Answer.text)
async def not_text(message: Message) -> None:
    await message.answer("Пришлите ответ обычным текстовым сообщением.")


@router.message()
async def fallback(message: Message) -> None:
    await message.answer(
        f"Работы студентов приходят сюда сами. Очередь открывает кнопка «{keyboards.BTN_QUEUE}».",
        reply_markup=keyboards.teacher_menu(),
    )
