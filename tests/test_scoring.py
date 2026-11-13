# Файл: tests/test_scoring.py
# Назначение: проверка разбора оценки «балл/максимум» и форматирования баллов.

import unittest

from bot.scoring import ScoreError, format_number, format_score, parse_score


class ParseScoreTest(unittest.TestCase):
    def test_integer_score(self):
        self.assertEqual(parse_score("7/10"), (7.0, 10.0))

    def test_spaces_are_ignored(self):
        self.assertEqual(parse_score("  7 / 10 "), (7.0, 10.0))

    def test_fraction_with_dot_and_comma(self):
        self.assertEqual(parse_score("7.5/10"), (7.5, 10.0))
        self.assertEqual(parse_score("7,5/10"), (7.5, 10.0))

    def test_word_separator(self):
        self.assertEqual(parse_score("7 из 10"), (7.0, 10.0))

    def test_zero_and_full_score_are_allowed(self):
        self.assertEqual(parse_score("0/10"), (0.0, 10.0))
        self.assertEqual(parse_score("10/10"), (10.0, 10.0))

    def test_wrong_format_is_rejected(self):
        for text in ["", "abc", "7", "7/", "/10", "7/10/2", "-1/10", "7/-10", "семь/десять"]:
            with self.subTest(text=text), self.assertRaises(ScoreError):
                parse_score(text)

    def test_score_above_maximum_is_rejected(self):
        with self.assertRaises(ScoreError):
            parse_score("11/10")

    def test_zero_maximum_is_rejected(self):
        with self.assertRaises(ScoreError):
            parse_score("0/0")


class FormatTest(unittest.TestCase):
    def test_whole_number_has_no_fraction(self):
        self.assertEqual(format_number(7.0), "7")

    def test_fraction_is_kept(self):
        self.assertEqual(format_number(7.5), "7.5")
        self.assertEqual(format_number(7.25), "7.25")

    def test_score_pair(self):
        self.assertEqual(format_score(7.5, 10.0), "7.5/10")


if __name__ == "__main__":
    unittest.main()
