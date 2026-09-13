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

    def test_budget_shows_what_was_swapped(self):
        """§8.3: чем именно заменили — обязательная часть ответа."""
        out = run("budget", "15500", "--period", "month")
        self.assertIn("8.3 КОРЗИНА ПОД 15 500 ₽", out)
        self.assertIn("Чем заменили", out)
        self.assertIn("Арза", out)          # Боржоми → Арза, −61%
        self.assertIn("итого:", out)

    def test_budget_reports_coverage_of_the_lever(self):
        """Сколько строк заменить было нечем и почему — это часть ответа, а не
        диагностика: «марка не распознана» пользователь может исправить сам."""
        out = run("budget", "2000")
        self.assertIn("Покрытие рычага", out)
        self.assertIn("словарь брендов", out)
        self.assertIn("весовой товар", out)

    def test_budget_says_when_the_basket_comes_out_empty(self):
        """«Уложились, запас 50 ₽» на пустой корзине — издёвка, а не ответ."""
        out = run("budget", "50")
        self.assertIn("КОРЗИНА ПУСТА", out)
        self.assertNotIn("запас", out)

    def test_basket_budget_points_at_8_3(self):
        """Два «бюджета» в одной оболочке обязаны объяснять разницу."""
        out = run("basket", "--budget", "2000")
        self.assertIn("Это 8.2", out)
        self.assertIn("budget 2 000", out)

    def test_smart_basket_states_the_comparison_window(self):
        """Сравниваются современники, и человек должен знать, за какой срок."""
        out = run("venues", "--basket", "week")
        self.assertIn("мес", out)

    def test_smart_basket_counts_every_basket_line(self):
        out = run("venues", "--basket", "week")
        self.assertIn("строкам из 8", out)

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

    def test_prices(self):
        out = run("prices", "--limit", "5")
        self.assertIn("8.5 ДИНАМИКА ЦЕН", out)
        self.assertIn("₽/кг", out)
        # оговорка про исключённые ряды обязана быть: без неё цифра выглядит
        # полнее, чем она есть
        self.assertIn("нераспознанной маркой", out)

    def test_prices_blended_says_what_it_shows(self):
        out = run("prices", "--all", "--blended", "--limit", "5")
        self.assertIn("смешанные ключи", out)

    def test_venues_comparison(self):
        out = run("venues", "--limit", "5")
        self.assertIn("8.6 ГДЕ ЧТО ДЕШЕВЛЕ", out)
        self.assertIn("Р-2", out)

    def test_venues_smart_basket_reports_coverage(self):
        """Экономия без доли разведённой корзины — половина правды."""
        out = run("venues", "--basket", "week")
        self.assertIn("8.6 УМНАЯ КОРЗИНА", out)
        self.assertIn("экономия", out)
        self.assertIn("корзины", out)
        self.assertIn("В экономию они не засчитаны", out)

    def test_choose_says_what_the_total_is_made_of(self):
        out = run("choose")
        self.assertIn("8.7 ВЫБОР МАГАЗИНА", out)
        self.assertIn("Итог сложен из: цена", out)
        self.assertIn("§8.7", out)

    def test_bad_dimension_is_rejected_by_the_parser(self):
        with self.assertRaises(SystemExit):
            run("spending", "--by", "колхоз")


if __name__ == "__main__":
    unittest.main()
