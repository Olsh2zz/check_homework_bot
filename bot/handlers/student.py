# Файл: bot/handlers/student.py
# Назначение: действия студента: сдача работы, список работ, отзыв сдачи,
# пересдача после доработки и вопрос по комментарию преподавателя.

from __future__ import annotations

from html import escape
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove, TelegramObject

from bot import db as storage
from bot import delivery, keyboards, texts
from bot.config import Config
from bot.db import Database
from bot.handlers.registration import ASK_NAME, MENU_HINT
from bot.states import Ask, Registration, Submit

router = Router(name="student")

MAX_TITLE_LENGTH = 100
MAX_QUESTION_LENGTH = 1500
# Ограничение на размер одной сдачи, чтобы преподавателю не пришли сотни сообщений.
MAX_ITEMS = 30
# Сколько последних альбомов помнить, чтобы отвечать на альбом один раз.
ALBUM_MEMORY = 500

COLLECT_HINT = (
    "Присылайте работу: текст, ссылки, фото или файлы, можно несколькими сообщениями. "
    f"Когда всё будет прислано, нажмите «{keyboards.BTN_SEND}»."
)

_seen_albums: list[str] = []


class RegisteredOnly(BaseMiddleware):
    """Пропускает к обработчикам только зарегистрированных студентов.

    Остальным предлагает регистрацию. Найденный студент передаётся обработчику
    в аргументе student.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data["event_from_user"]
        student = await data["db"].get_student(user.id)
        if student is None:
            await data["state"].set_state(Registration.full_name)
            await data["bot"].send_message(
                user.id,
                "Сначала нужно зарегистрироваться.\n\n" + ASK_NAME,
                reply_markup=ReplyKeyboardRemove(),
            )
            if isinstance(event, CallbackQuery):
                await event.answer()
            return None
        data["student"] = student
        return await handler(event, data)


router.message.middleware(RegisteredOnly())
router.callback_query.middleware(RegisteredOnly())


# ---------- Меню ----------


@router.message(F.text == keyboards.BTN_CANCEL)
async def cancel(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    await db.delete_draft(message.from_user.id)
    await message.answer("Отменено. " + MENU_HINT, reply_markup=keyboards.student_menu())


@router.message(StateFilter(None), F.text == keyboards.BTN_SUBMIT)
async def submit_start(message: Message, state: FSMContext, db: Database) -> None:
    await db.delete_draft(message.from_user.id)
    await state.set_state(Submit.title)
    await message.answer(
        "Как называется работа? Например: Лабораторная 3",
        reply_markup=keyboards.cancel_only(),
    )


@router.message(StateFilter(None), F.text == keyboards.BTN_MY)
async def my_works(message: Message, db: Database) -> None:
    await _show_works(message.bot, message.from_user.id, db)


@router.message(StateFilter(None), F.text == keyboards.BTN_PROFILE)
async def profile(message: Message, student) -> None:
    await message.answer(texts.profile(student), reply_markup=keyboards.edit_profile())


async def _show_works(bot: Bot, student_id: int, db: Database) -> None:
    works = await db.list_works(student_id)
    shown = works[: texts.MAX_WORKS_SHOWN]
    pending = [work for work in shown if work["status"] == storage.STATUS_PENDING]
    rejected = [work for work in shown if work["status"] == storage.STATUS_REJECTED]
    await bot.send_message(
        student_id,
        texts.works_list(works),
        reply_markup=keyboards.works_actions(pending, rejected),
    )


# ---------- Сдача работы ----------


@router.message(Submit.title, F.text)
async def submit_title(message: Message, state: FSMContext, db: Database) -> None:
    title = " ".join(message.text.split())
    if not title:
        await message.answer("Название не может быть пустым. Напишите ещё раз.")
        return
    if len(title) > MAX_TITLE_LENGTH:
        await message.answer(
            f"Название должно быть не длиннее {MAX_TITLE_LENGTH} символов. Напишите короче."
        )
        return
    await db.create_draft(message.from_user.id, title)
    await state.set_state(Submit.collecting)
    await message.answer(COLLECT_HINT, reply_markup=keyboards.collecting())


@router.message(Submit.title)
async def submit_title_not_text(message: Message) -> None:
    await message.answer("Название нужно прислать текстом.")


@router.message(Submit.collecting, F.text == keyboards.BTN_SEND)
async def submit_finish(
    message: Message, state: FSMContext, db: Database, config: Config, bot: Bot
) -> None:
    draft = await db.get_draft(message.from_user.id)
    if draft is None:
        await state.clear()
        await message.answer(
            "Черновик не найден. Начните сдачу заново.", reply_markup=keyboards.student_menu()
        )
        return
    if await db.count_items(draft["id"]) == 0:
        await message.answer("В работе пока ничего нет. Сначала пришлите текст, фото или файл.")
        return
    await db.submit_draft(draft["id"])
    await state.clear()
    delivered = await delivery.send_to_teacher(bot, db, config, draft["id"])
    title = escape(draft["title"])
    if delivered:
        text = f"⏳ Работа «{title}» отправлена на проверку. Результат придёт сюда."
    else:
        text = (
            f"⏳ Работа «{title}» сохранена и стоит в очереди проверки, "
            "но уведомить преподавателя сейчас не удалось."
        )
    await message.answer(text, reply_markup=keyboards.student_menu())


@router.message(Submit.collecting)
async def submit_collect(message: Message, state: FSMContext, db: Database) -> None:
    await _collect(message, state, db)


async def _collect(message: Message, state: FSMContext, db: Database) -> None:
    """Добавляет присланное сообщение в черновик сдачи."""
    draft = await db.get_draft(message.from_user.id)
    if draft is None:
        await state.clear()
        await message.answer(
            "Черновик не найден. Начните сдачу заново.", reply_markup=keyboards.student_menu()
        )
        return

    if message.text:
        kind, text, file_id = storage.KIND_TEXT, message.html_text, None
    elif message.photo:
        # Telegram присылает несколько размеров фото, последний из них самый большой.
        kind, file_id = storage.KIND_PHOTO, message.photo[-1].file_id
        text = message.html_text if message.caption else None
    elif message.document:
        kind, file_id = storage.KIND_DOCUMENT, message.document.file_id
        text = message.html_text if message.caption else None
    else:
        await message.answer(
            "Такой тип сообщения не принимается. Пришлите текст, фото или файл "
            "(видео и аудио можно отправить как файл)."
        )
        return

    if await db.count_items(draft["id"]) >= MAX_ITEMS:
        await message.answer(
            f"В одной работе может быть не больше {MAX_ITEMS} сообщений. "
            f"Нажмите «{keyboards.BTN_SEND}»."
        )
        return
    count = await db.add_item(draft["id"], message.message_id, kind, text, file_id)

    album = message.media_group_id
    if album is None:
        await message.answer(f"Добавлено, сообщений в работе: {count}.")
    elif album not in _seen_albums:
        # Альбом приходит отдельными сообщениями, отвечаем только на первое из них.
        _seen_albums.append(album)
        del _seen_albums[:-ALBUM_MEMORY]
        await message.answer("Альбом добавлен.")


# ---------- Кнопки под работами ----------


@router.callback_query(keyboards.WorkCb.filter(F.action == keyboards.ACTION_WITHDRAW))
async def withdraw(
    callback: CallbackQuery,
    callback_data: keyboards.WorkCb,
    db: Database,
    config: Config,
    bot: Bot,
) -> None:
    if not await db.withdraw(callback_data.sid, callback.from_user.id):
        await callback.answer("Эту работу уже нельзя отозвать: она проверена.", show_alert=True)
        return
    await delivery.refresh_teacher_card(bot, db, config, callback_data.sid)
    await callback.answer("Работа отозвана")
    await _show_works(bot, callback.from_user.id, db)


@router.callback_query(keyboards.WorkCb.filter(F.action == keyboards.ACTION_FIX))
async def fix(
    callback: CallbackQuery,
    callback_data: keyboards.WorkCb,
    state: FSMContext,
    db: Database,
    bot: Bot,
) -> None:
    student_id = callback.from_user.id
    if not await db.can_resubmit(callback_data.sid, student_id):
        await callback.answer(
            "Эту работу пересдать нельзя: по ней уже есть более новая попытка.",
            show_alert=True,
        )
        return
    sub = await db.get_submission(callback_data.sid)
    await db.create_draft(student_id, sub["title"], parent_id=sub["id"])
    await state.set_state(Submit.collecting)
    await bot.send_message(
        student_id,
        f"Пересдача работы «{escape(sub['title'])}». " + COLLECT_HINT,
        reply_markup=keyboards.collecting(),
    )
    await callback.answer()


@router.callback_query(keyboards.WorkCb.filter(F.action == keyboards.ACTION_ASK))
async def ask(
    callback: CallbackQuery,
    callback_data: keyboards.WorkCb,
    state: FSMContext,
    db: Database,
    bot: Bot,
) -> None:
    sub = await db.get_submission(callback_data.sid)
    if sub is None or sub["student_id"] != callback.from_user.id:
        await callback.answer("Работа не найдена.", show_alert=True)
        return
    await db.delete_draft(callback.from_user.id)
    await state.set_state(Ask.text)
    await state.update_data(sid=sub["id"])
    await bot.send_message(
        callback.from_user.id,
        f"Напишите вопрос по работе «{escape(sub['title'])}» одним сообщением.",
        reply_markup=keyboards.cancel_only(),
    )
    await callback.answer()


@router.message(Ask.text, F.text)
async def ask_text(
    message: Message, state: FSMContext, db: Database, config: Config, bot: Bot
) -> None:
    question = message.text.strip()
    if len(question) > MAX_QUESTION_LENGTH:
        await message.answer(
            f"Вопрос должен быть не длиннее {MAX_QUESTION_LENGTH} символов. Сократите его."
        )
        return
    data = await state.get_data()
    sub = await db.get_submission(data["sid"])
    await state.clear()
    question_id = await db.create_question(sub["id"], question)
    try:
        await bot.send_message(
            config.teacher_id,
            texts.question_for_teacher(sub, question),
            reply_markup=keyboards.answer_question(question_id),
        )
    except TelegramAPIError:
        await message.answer(
            "Не удалось передать вопрос преподавателю. Попробуйте позже.",
            reply_markup=keyboards.student_menu(),
        )
        return
    await message.answer(
        "Вопрос отправлен преподавателю. Ответ придёт сюда.",
        reply_markup=keyboards.student_menu(),
    )


@router.message(Ask.text)
async def ask_not_text(message: Message) -> None:
    await message.answer("Вопрос нужно прислать текстом.")


# ---------- Всё остальное ----------


@router.message()
async def fallback(
    message: Message, state: FSMContext, db: Database, config: Config, bot: Bot
) -> None:
    """Сообщение вне диалога.

    Если у студента остался черновик (бот перезапускали во время сдачи),
    сбор работы продолжается, иначе показывается меню.
    """
    if await db.get_draft(message.from_user.id) is not None:
        await state.set_state(Submit.collecting)
        if message.text == keyboards.BTN_SEND:
            await submit_finish(message, state, db, config, bot)
        else:
            await _collect(message, state, db)
        return
    await message.answer(MENU_HINT, reply_markup=keyboards.student_menu())
