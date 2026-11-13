# Файл: bot/handlers/registration.py
# Назначение: команда /start и регистрация студента: ввод ФИО и группы,
# изменение этих данных из профиля.

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from bot import keyboards
from bot.db import Database
from bot.states import Registration

router = Router(name="registration")

MIN_NAME_WORDS = 2
MAX_NAME_LENGTH = 100
MAX_GROUP_LENGTH = 20

ASK_NAME = "Напишите фамилию, имя и отчество, например: Иванов Иван Иванович"
ASK_GROUP = "Теперь напишите номер группы, например: ИВТ-21"
MENU_HINT = "Выберите действие на клавиатуре ниже."


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    await db.delete_draft(message.from_user.id)
    student = await db.get_student(message.from_user.id)
    if student is not None:
        await message.answer(
            f"Здравствуйте, {student['full_name']}! {MENU_HINT}",
            reply_markup=keyboards.student_menu(),
            parse_mode=None,
        )
        return
    await state.set_state(Registration.full_name)
    await message.answer(
        "Здравствуйте! Через этого бота сдаются домашние работы. "
        "Сначала зарегистрируйтесь.\n\n" + ASK_NAME,
        reply_markup=ReplyKeyboardRemove(),
    )


@router.callback_query(keyboards.ProfileCb.filter())
async def edit_profile(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await state.set_state(Registration.full_name)
    await bot.send_message(
        callback.from_user.id, ASK_NAME, reply_markup=ReplyKeyboardRemove()
    )
    await callback.answer()


@router.message(Registration.full_name, F.text)
async def full_name(message: Message, state: FSMContext) -> None:
    name = " ".join(message.text.split())
    if len(name.split(" ")) < MIN_NAME_WORDS:
        await message.answer("Нужны хотя бы фамилия и имя через пробел. Напишите ещё раз.")
        return
    if len(name) > MAX_NAME_LENGTH:
        await message.answer(f"ФИО должно быть не длиннее {MAX_NAME_LENGTH} символов.")
        return
    await state.update_data(full_name=name)
    await state.set_state(Registration.group)
    await message.answer(ASK_GROUP)


@router.message(Registration.group, F.text)
async def group(message: Message, state: FSMContext, db: Database) -> None:
    group_name = " ".join(message.text.split())
    if not group_name or len(group_name) > MAX_GROUP_LENGTH:
        await message.answer(
            f"Номер группы должен быть от 1 до {MAX_GROUP_LENGTH} символов. Напишите ещё раз."
        )
        return
    data = await state.get_data()
    await db.save_student(
        message.from_user.id, message.from_user.username, data["full_name"], group_name
    )
    await state.clear()
    await message.answer(
        f"Готово, данные сохранены. {MENU_HINT}", reply_markup=keyboards.student_menu()
    )


@router.message(Registration.full_name)
@router.message(Registration.group)
async def not_text(message: Message) -> None:
    await message.answer("Пришлите ответ обычным текстовым сообщением.")
