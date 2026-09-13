"""Телеграм-бот: транспорт, а не агент (DECISIONS ОА-1).

ОА-1 сформулировал три проверяемых запрета, и здесь они проверяются буквально —
чтением исходников бота. Это не педантизм: запрет, который никто не проверяет,
через два спринта нарушается «на минутку», и появляется вторая копия правды.

Сам aiogram в тестах не поднимается: бот не содержит логики, проверять в нём
нечего, кроме запретов и оформления.
"""

import ast
import os
import re
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from bot import api, format as fmt                              # noqa: E402

SOURCES = ("bot/__init__.py", "bot/api.py", "bot/format.py", "bot/main.py",
           "bot/__main__.py")


def source(path):
    with open(os.path.join(BASE, path), encoding="utf-8") as f:
        return f.read()


class ConstraintsTest(unittest.TestCase):
    """Три запрета ОА-1."""

    def test_bot_does_not_write_to_disk(self):
        # По границе слова: `urlopen(` — это сеть, а не файл, и запрещать его
        # подстрокой «open(» значит запрещать не то.
        forbidden = (r"\bopen\(", r"\bsqlite3\b", r"\bshelve\b", r"\bpickle\b",
                     r"\bjson\.dump\(", r"\bPath\(", r"\bmakedirs\b")
        for path in SOURCES:
            text = source(path)
            for pattern in forbidden:
                self.assertIsNone(re.search(pattern, text), f"{path}: {pattern}")

    def test_bot_does_not_import_the_core(self):
        """Бот знает ядро только через HTTP: иначе он начнёт считать сам."""
        for path in SOURCES:
            text = source(path)
            self.assertNotIn("from agent", text, path)
            self.assertNotIn("import agent", text, path)

    def test_bot_holds_no_user_state_between_updates(self):
        """Ни словаря по chat_id, ни кэша профиля — состояние живёт в ядре."""
        text = source("bot/main.py")
        self.assertNotIn("global ", text)
        for suspicious in ("_cache", "sessions =", "users =", "state ="):
            self.assertNotIn(suspicious, text)

    def test_bot_contains_no_arithmetic_over_core_numbers(self):
        """Логики нет: числа приходят готовыми и только переносятся в текст.

        Проверяется разбором синтаксиса, а не поиском подстроки: «+» в боте есть
        и должен быть — это склейка строк. А вот вычитание, умножение и деление
        над числами ядра означали бы, что бот что-то досчитывает, и это прямой
        обход ОА-1.
        """
        arithmetic = (ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
        for path in SOURCES:
            tree = ast.parse(source(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.BinOp):
                    self.assertNotIsInstance(node.op, arithmetic,
                                             f"{path}:{node.lineno}")


class ApiClientTest(unittest.TestCase):
    """Клиент ядра: недоступность — это сообщение, а не падение."""

    def test_core_error_on_unreachable_core(self):
        with self.assertRaises(api.CoreError):
            api.get("/api/scenarios", base="http://127.0.0.1:9", timeout=0.2)

    def test_url_is_built_from_configuration(self):
        self.assertTrue(api.CORE_URL.startswith("http"))


class FormatTest(unittest.TestCase):
    """Оформление: перенос строк ядра, ничего больше."""

    def test_unknown_query_offers_buttons(self):
        text = fmt.answer({"understood": False, "reason": "не понял запрос"})
        self.assertIn("Не понял", text)

    def test_missing_parameter_asks_for_it(self):
        text = fmt.answer({"understood": True, "missing": ["amount"]})
        self.assertIn("не хватает", text)
        self.assertIn("уложись в 2000", text)

    def test_facts_are_shown_verbatim(self):
        facts = "экономия, ₽: 334\nразведено строк: 4"
        text = fmt.answer({"understood": True, "facts": facts})
        self.assertIn("334", text)
        self.assertIn("4", text)

    def test_model_explanation_comes_first(self):
        text = fmt.answer({"understood": True, "text": "Дешевле в Глобусе.",
                           "facts": "итого, ₽: 1 246"})
        self.assertLess(text.index("Дешевле"), text.index("1 246"))

    def test_html_is_escaped(self):
        text = fmt.answer({"understood": True, "facts": "<b>не разметка</b>"})
        self.assertIn("&lt;b&gt;", text)

    def test_long_answer_is_cut_not_dropped(self):
        text = fmt.answer({"understood": True, "facts": "строка\n" * 2000})
        self.assertLessEqual(len(text), fmt.MAX_CHARS + 1)

    def test_reminder_is_empty_when_nothing_is_due(self):
        self.assertIsNone(fmt.reminder([]))

    def test_reminder_names_the_group_and_the_gap(self):
        text = fmt.reminder([{"group": "kefir", "label": "ЭкоНива",
                              "days_since": 53, "median_gap_days": 6}])
        self.assertIn("kefir", text)
        self.assertIn("53", text)
        self.assertIn("6", text)

    def test_plan_list_prints_the_list_and_marks_defaulted_shops(self):
        """Список покупок доходит до текста вместе с оговоркой про магазин."""
        text = fmt.outgoing({"kind": "plan", "payload": {
            "title": "Продукты на неделю", "variant": "Врозь по магазинам",
            "venues": [{"venue": "Глобус", "lines": [
                {"label": "ЛУК", "amount": "527 ₽", "chosen": True,
                 "reason": None},
                {"label": "СМЕТАНА", "amount": "185 ₽ обычно", "chosen": False,
                 "reason": "марка не распознана"}]}],
            "notes": ["Магазин выбран у 3 из 8 строк."]}})
        self.assertIn("Глобус", text)
        self.assertIn("527 ₽", text)
        self.assertIn("185 ₽ обычно", text)
        self.assertIn("марка не распознана", text)
        self.assertIn("3 из 8", text)

    def test_unknown_message_kind_is_not_sent_as_garbage(self):
        self.assertIsNone(fmt.outgoing({"kind": "неизвестно", "payload": {}}))
        self.assertIsNone(fmt.outgoing(None))

    def test_web_link_is_added_when_configured(self):
        text = fmt.answer({"understood": True, "facts": "a: 1"},
                          web_url="http://example/venues")
        self.assertIn("http://example/venues", text)


if __name__ == "__main__":
    unittest.main()
