"""Сценарий «продукты на неделю» целиком (agent/plan.py).

Здесь проверяется не арифметика — её считают 8.2, 8.3 и 8.6, и у каждой свои
тесты, — а честность совмещения. Совмещать три ответа в один экран опасно ровно
тремя способами, и на каждый стоит тест:

*Сложить разные основания.* Строка с известным магазином посчитана в ценах окна
сравнения, строка без магазина — по медиане трат за всю историю. На датасете это
1 465 ₽ против 1 040 ₽ по одним и тем же трём строкам. Общего итога у варианта
нет, и тест это закрепляет.

*Выдать умолчание за выбор.* Магазин стоит у каждой строки списка, иначе список
неполон. Но у пяти строк из восьми он проставлен за неимением сравнения, и
`venue_known` обязан их отличать, а причина — называться.

*Посчитать варианты по разным строкам.* Разность двух сценариев перестаёт быть
экономией, если наборы строк разошлись (Р-25). Поэтому экономия здесь не
считается заново, а берётся у 8.6, и тест сверяет, что она равна разности.
"""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fixture  # noqa: E402

from agent import plan as PL                                  # noqa: E402
from agent.matching import from_history as catalog_from_history  # noqa: E402
from agent.profile import for_period, smart_basket            # noqa: E402
from agent.settings import Settings                           # noqa: E402


class PlanTest(unittest.TestCase):
    """План на датасете: варианты, покрытие, список по магазинам."""

    @classmethod
    def setUpClass(cls):
        cls.history = fixture.history()
        cls.profile = fixture.profile()
        cls.settings = Settings()
        cls.catalog = catalog_from_history(cls.history)

    def plan(self, **kwargs):
        kwargs.setdefault("budget", 2000)
        return PL.build(self.history, self.profile, self.catalog,
                        settings=self.settings, **kwargs)

    # --- строки не теряются и не задваиваются ---

    def test_every_basket_line_lands_in_exactly_one_outcome(self):
        """Каждая строка корзины учтена ровно один раз — в списке или в отказе.

        Инвариант печатаемого (Р-25): если строка потеряется, итог всё равно
        сойдётся сам с собой, и заметить это по числам нельзя.
        """
        plan = self.plan()
        for variant in plan.variants:
            groups = [l.group for l in variant.lines]
            groups += [l.group for l in variant.dropped]
            self.assertEqual(
                sorted(groups), sorted(l.group for l in plan.basket.lines),
                f"{variant.id}: строки корзины разошлись со строками варианта")
            self.assertEqual(len(groups), len(set(groups)),
                             f"{variant.id}: строка учтена дважды")

    def test_priced_and_unpriced_split_the_whole_variant(self):
        plan = self.plan()
        for variant in plan.variants:
            self.assertEqual(
                len(variant.priced_lines) + len(variant.unpriced_lines),
                len(variant.lines), f"{variant.id}: строка ни там, ни там")

    # --- экономия не пересчитывается, а берётся у 8.6 ---

    def test_saving_equals_the_difference_between_the_two_variants(self):
        """Экономия врозь — это разность сравнимых частей, а не своё число.

        Оба сценария обязаны считаться по одним и тем же строкам, иначе
        разность перестаёт быть экономией (Р-25).
        """
        plan = self.plan()
        single, split = plan.get("single"), plan.get("split")
        self.assertAlmostEqual(single.priced_total - split.priced_total,
                               split.saving, places=6)
        self.assertAlmostEqual(split.saving, single.overpay, places=6)

    def test_split_is_never_dearer_than_one_place(self):
        """«Врозь» не дороже, чем «в одном месте», и экономия не отрицательна."""
        plan = self.plan()
        single, split = plan.get("single"), plan.get("split")
        self.assertLessEqual(split.priced_total, single.priced_total)
        self.assertGreaterEqual(split.saving, 0)

    def test_both_variants_are_counted_on_the_same_lines(self):
        plan = self.plan()
        single, split = plan.get("single"), plan.get("split")
        self.assertEqual(sorted(l.group for l in single.priced_lines),
                         sorted(l.group for l in split.priced_lines))
        self.assertAlmostEqual(single.usual_total, split.usual_total, places=6)

    def test_one_place_part_is_the_route_baseline(self):
        """Сравнимая часть «в одном месте» — это база 8.6, а не своя сумма."""
        plan = self.plan()
        route = smart_basket(self.history,
                             for_period(self.profile, "week", self.settings))
        self.assertAlmostEqual(plan.get("single").priced_total,
                               route.baseline_total, places=6)
        self.assertAlmostEqual(plan.get("split").priced_total,
                               route.split_total, places=6)

    # --- умолчание отличимо от выбора ---

    def test_line_without_comparison_says_so_and_names_the_reason(self):
        """Магазин есть у каждой строки, но выбран не у каждой.

        Без этого различия список выглядит как совет там, где выбора не было.
        """
        plan = self.plan()
        for variant in plan.variants:
            for line in variant.lines:
                self.assertIsNotNone(line.venue,
                                     f"{variant.id}/{line.group}: строка без магазина")
                if line.venue_known:
                    self.assertIsNone(line.reason)
                    self.assertIsNotNone(line.cost)
                else:
                    self.assertIsNotNone(
                        line.reason,
                        f"{variant.id}/{line.group}: магазин проставлен молча")
                    self.assertIsNone(line.cost)

    def test_reasons_cover_every_undiluted_line(self):
        plan = self.plan()
        self.assertEqual(len(plan.unpriced_reasons),
                         plan.considered - plan.priced)
        for line, reason in plan.unpriced_reasons:
            self.assertIn(reason, (PL.NO_TYPICAL, PL.NO_UNIT_PRICE, PL.BLENDED,
                                   PL.ONE_VENUE, PL.NOT_AT_SINGLE))

    def test_coverage_denominator_is_the_whole_basket(self):
        """Доля разведённого считается от всех строк корзины (Р-13)."""
        plan = self.plan()
        self.assertEqual(plan.considered, len(plan.basket.lines))
        self.assertAlmostEqual(plan.covered, plan.priced / plan.considered)
        self.assertLess(plan.covered, 1.0,
                        "на датасете разводится не вся корзина — "
                        "если это изменилось, менять надо и текст про покрытие")

    # --- два основания, и они не складываются ---

    def test_the_two_bases_are_kept_apart_and_measured(self):
        """Цена по магазину и обычная трата — разные деньги, и разрыв измерен."""
        plan = self.plan()
        single = plan.get("single")
        self.assertAlmostEqual(
            plan.price_gap, single.priced_total - plan.usual_of_priced, places=6)
        # Разрыв считается по ОДНИМ И ТЕМ ЖЕ строкам — тем, что удалось оценить.
        self.assertEqual(len(single.priced_lines), plan.priced)
        self.assertGreater(
            plan.price_gap, 0,
            "цены окна не выше обычной траты: если это так, оговорка про "
            "разные деньги стала неверной и её надо переписать")

    def test_variant_has_no_grand_total(self):
        """У варианта нет поля с общим итогом — сложить основания нечем.

        Запрет выражен структурой, а не памятью (Р-9): пока такого поля нет,
        напечатать бессмысленную сумму неоткуда.
        """
        for name in ("total", "grand_total", "amount"):
            self.assertFalse(hasattr(PL.Variant, name),
                             f"у варианта появился {name}: два основания "
                             f"сложены в одно число")

    # --- вариант под сумму ---

    def test_budget_variant_drops_needs_and_says_which(self):
        plan = self.plan()
        budget = plan.get("budget")
        self.assertTrue(budget.dropped, "на датасете под 2000 ₽ что-то выбывает")
        self.assertAlmostEqual(budget.dropped_amount,
                               sum(l.amount for l in budget.dropped), places=6)
        kept = {l.group for l in budget.lines}
        for line in budget.dropped:
            self.assertNotIn(line.group, kept,
                             "выброшенная строка осталась в списке")

    def test_budget_variant_is_absent_without_a_sum(self):
        """Сумма не подставляется молча: «под 2000» и «под 3000» — разные ответы."""
        plan = PL.build(self.history, self.profile, self.catalog,
                        settings=self.settings)
        self.assertIsNone(plan.get("budget"))
        self.assertEqual([v.id for v in plan.variants], ["single", "split"])

    def test_asking_for_a_missing_variant_is_not_silently_substituted(self):
        """Попросили «под сумму» без суммы — показан другой, и это видно (Р-23)."""
        plan = PL.build(self.history, self.profile, self.catalog,
                        settings=self.settings, chosen="budget")
        self.assertEqual(plan.chosen, "single")
        self.assertEqual(plan.requested, "budget")
        self.assertFalse(plan.honoured)

    def test_choice_is_honoured_when_the_variant_exists(self):
        plan = self.plan(chosen="split")
        self.assertEqual(plan.chosen, "split")
        self.assertTrue(plan.honoured)
        self.assertIs(plan.variant, plan.get("split"))

    # --- готовый список ---

    def test_list_by_venue_covers_every_line_once(self):
        plan = self.plan(chosen="split")
        by_venue = plan.variant.by_venue()
        groups = [l.group for _venue, lines in by_venue for l in lines]
        self.assertEqual(sorted(groups),
                         sorted(l.group for l in plan.variant.lines))
        self.assertEqual(len(groups), len(set(groups)))

    def test_split_really_sends_you_to_more_than_one_shop(self):
        """Иначе «врозь» и «в одном месте» — один и тот же ответ под двумя именами."""
        plan = self.plan()
        self.assertGreater(plan.get("split").stops, plan.get("single").stops)
        self.assertEqual(plan.get("single").stops, 1)

    def test_unpriced_lines_go_to_the_main_venue_when_split(self):
        """Строку без сравнения везти некуда, кроме основного магазина."""
        plan = self.plan(chosen="split")
        for line in plan.get("split").unpriced_lines:
            self.assertEqual(line.venue, plan.main_venue)


if __name__ == "__main__":
    unittest.main()
