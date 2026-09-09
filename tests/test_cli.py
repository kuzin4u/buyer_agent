"""Оболочка командной строки: каждая функция С2 доходит до печати.

CLI ничего не считает сам — он печатает то, что посчитало ядро. Поэтому здесь
проверяется не арифметика (она в своих тестах), а что команды не падают и
показывают то, что обещают.
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fixture  # noqa: E402,F401  (кэш прогона)
import cli      # noqa: E402


def run(*argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli.main(list(argv))
    assert code == 0
    return buf.getvalue()


class CliTest(unittest.TestCase):
    def test_profile(self):
        out = run("profile")
        self.assertIn("8.1 ПРОФИЛЬ ПРЕДПОЧТЕНИЙ", out)
        self.assertIn("Глобус", out)
        self.assertIn("Постоянная корзина", out)

    def test_basket_period_and_budget(self):
        week = run("basket", "--period", "week")
        self.assertIn("8.2 КОРЗИНА", week)
        self.assertIn("Итого:", week)
        budget = run("basket", "--budget", "1500")
        self.assertIn("1 500", budget)

    def test_lapsed(self):
        out = run("lapsed")
        self.assertIn("8.4 ЧТО ДАВНО НЕ ПОКУПАЛ", out)
        self.assertIn("kefir", out)

    def test_lapsed_on_empty_horizon_says_so(self):
        """Если всё в срок, команда должна сказать это, а не молчать."""
        out = run("lapsed", "--asof", "2017-07-01")
        self.assertIn("8.4", out)

    def test_spending_dimensions(self):
        for dim in ("year", "venue", "dept", "group", "segment"):
            out = run("spending", "--by", dim)
            self.assertIn("8.8 КОНТРОЛЬ ТРАТ", out)
            self.assertIn("5 583 901", out)

    def test_spending_growth(self):
        out = run("spending", "--growth", "--by", "group", "--limit", "5")
        self.assertIn("ГДЕ ТРАТЫ РАСТУТ", out)
        self.assertIn("§8.5", out)

    def test_bad_dimension_is_rejected_by_the_parser(self):
        with self.assertRaises(SystemExit):
            run("spending", "--by", "колхоз")


if __name__ == "__main__":
    unittest.main()
