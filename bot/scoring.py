# Файл: bot/scoring.py
# Назначение: разбор оценки вида «7/10», которую вводит преподаватель,
# и форматирование баллов для сообщений.

from __future__ import annotations

import re

# Допускаются целые и дробные числа, разделитель дроби: точка или запятая.
# Между баллом и максимумом стоит «/» или слово «из».
_SCORE_RE = re.compile(
    r"^\s*(\d+(?:[.,]\d+)?)\s*(?:/|из)\s*(\d+(?:[.,]\d+)?)\s*$",
    re.IGNORECASE,
)


class ScoreError(ValueError):
    """Оценка введена неверно: текст ошибки показывается преподавателю."""


def parse_score(text: str) -> tuple[float, float]:
    """Разбирает строку «балл/максимум». Максимум больше нуля, балл от 0 до максимума."""
    match = _SCORE_RE.match(text or "")
    if match is None:
        raise ScoreError("Нужен формат «балл/максимум», например 7/10")
    score = float(match.group(1).replace(",", "."))
    max_score = float(match.group(2).replace(",", "."))
    if max_score <= 0:
        raise ScoreError("Максимальный балл должен быть больше нуля")
    if score > max_score:
        raise ScoreError("Балл не может быть больше максимума")
    return score, max_score


def format_number(value: float) -> str:
    """Убирает лишнюю дробную часть: 7.0 превращается в «7», 7.5 остаётся «7.5»."""
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def format_score(score: float, max_score: float) -> str:
    return f"{format_number(score)}/{format_number(max_score)}"
