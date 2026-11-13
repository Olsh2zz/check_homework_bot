# Файл: bot/texts.py
# Назначение: сборка текстов сообщений: карточка работы для преподавателя,
# результат для студента, список работ, очередь, вопрос и ответ.

from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from html import escape

from bot import db as storage
from bot.scoring import format_number, format_score

# В одно сообщение Telegram помещается 4096 символов, поэтому длинные списки обрезаются.
MAX_WORKS_SHOWN = 30
MAX_QUEUE_SHOWN = 30

STATUS_ICONS = {
    storage.STATUS_PENDING: "⏳",
    storage.STATUS_ACCEPTED: "✅",
    storage.STATUS_REJECTED: "❌",
    storage.STATUS_WITHDRAWN: "↩️",
}


def format_dt(iso: str, tz: tzinfo) -> str:
    """Переводит время из базы (UTC, ISO 8601) в местное «13.11.2026 10:05»."""
    return datetime.fromisoformat(iso).astimezone(tz).strftime("%d.%m.%Y %H:%M")


def waiting_days(iso: str, now: datetime) -> int:
    return max((now - datetime.fromisoformat(iso)).days, 0)


def _score_of(sub) -> str | None:
    if sub["score"] is None:
        return None
    return format_score(sub["score"], sub["max_score"])


def _verdict_line(sub) -> str:
    status = sub["status"]
    score = _score_of(sub)
    if status == storage.STATUS_ACCEPTED:
        return f"✅ Принято: {score}" if score else "✅ Принято"
    if status == storage.STATUS_REJECTED:
        return f"❌ На доработку: {score}" if score else "❌ На доработку"
    if status == storage.STATUS_WITHDRAWN:
        return "↩️ Отозвана студентом"
    return "⏳ Ждёт проверки"


def submission_card(sub, previous_comment: str | None, tz: tzinfo) -> str:
    """Карточка работы в чате преподавателя. sub: строка сдачи с полями студента."""
    student = f"{escape(sub['full_name'])} ({escape(sub['group_name'])})"
    if sub["username"]:
        student += f", @{escape(sub['username'])}"
    lines = [
        f"📄 <b>{escape(sub['title'])}</b>",
        f"Студент: {student}",
        f"Попытка: {sub['attempt']}",
        f"Сдано: {format_dt(sub['created_at'], tz)}",
    ]
    if previous_comment:
        lines.append(f"Прошлый комментарий: {escape(previous_comment)}")
    lines += ["", _verdict_line(sub)]
    if sub["comment"]:
        lines.append(f"Комментарий: {escape(sub['comment'])}")
    return "\n".join(lines)


def result_for_student(sub) -> str:
    title = escape(sub["title"])
    if sub["status"] == storage.STATUS_ACCEPTED:
        lines = [f"✅ Работа «{title}» принята"]
    else:
        lines = [f"❌ Работу «{title}» нужно доработать"]
    score = _score_of(sub)
    if score:
        lines.append(f"Баллы: {score}")
    if sub["comment"]:
        lines.append(f"Комментарий: {escape(sub['comment'])}")
    return "\n".join(lines)


def works_list(works: list) -> str:
    """Список работ студента с суммой баллов по принятым работам."""
    if not works:
        return "У вас пока нет сданных работ."
    lines = ["<b>Ваши работы</b>", ""]
    for work in works[:MAX_WORKS_SHOWN]:
        line = f"{STATUS_ICONS[work['status']]} {escape(work['title'])}"
        score = _score_of(work)
        if score:
            line += f": {score}"
        if work["attempt"] > 1:
            line += f" (попытка {work['attempt']})"
        lines.append(line)
    if len(works) > MAX_WORKS_SHOWN:
        lines.append(f"… и ещё {len(works) - MAX_WORKS_SHOWN}")

    accepted = [
        work
        for work in works
        if work["status"] == storage.STATUS_ACCEPTED and work["score"] is not None
    ]
    if accepted:
        total = sum(work["score"] for work in accepted)
        maximum = sum(work["max_score"] for work in accepted)
        lines += ["", f"Сумма баллов за принятые работы: {format_score(total, maximum)}"]
    lines += ["", "⏳ на проверке, ✅ принято, ❌ на доработку"]
    return "\n".join(lines)


def queue_list(submissions: list, now: datetime | None = None) -> str:
    if not submissions:
        return "Очередь пуста: все работы проверены."
    moment = now or datetime.now(timezone.utc)
    lines = [f"<b>Ждут проверки: {len(submissions)}</b>", ""]
    for index, sub in enumerate(submissions[:MAX_QUEUE_SHOWN], start=1):
        days = waiting_days(sub["created_at"], moment)
        wait = "сегодня" if days == 0 else f"{days} дн."
        lines.append(
            f"{index}. {escape(sub['full_name'])} ({escape(sub['group_name'])}): "
            f"{escape(sub['title'])}, {wait}"
        )
    if len(submissions) > MAX_QUEUE_SHOWN:
        lines.append(f"… и ещё {len(submissions) - MAX_QUEUE_SHOWN}")
    lines += ["", "Нажмите на работу, чтобы открыть её."]
    return "\n".join(lines)


def question_for_teacher(sub, question: str) -> str:
    lines = [
        f"❓ Вопрос по работе «{escape(sub['title'])}»",
        f"Студент: {escape(sub['full_name'])} ({escape(sub['group_name'])})",
    ]
    if sub["comment"]:
        lines.append(f"Ваш комментарий: {escape(sub['comment'])}")
    lines += ["", f"Вопрос: {escape(question)}"]
    return "\n".join(lines)


def answer_for_student(sub, question: str, answer: str) -> str:
    return "\n".join(
        [
            f"💬 Ответ преподавателя по работе «{escape(sub['title'])}»",
            f"Ваш вопрос: {escape(question)}",
            "",
            f"Ответ: {escape(answer)}",
        ]
    )


def profile(student) -> str:
    return (
        f"ФИО: {escape(student['full_name'])}\n"
        f"Группа: {escape(student['group_name'])}"
    )


def reminder(count: int, days: int) -> str:
    return f"⏰ Работ, которые ждут проверки дольше {format_number(days)} дн.: {count}"
