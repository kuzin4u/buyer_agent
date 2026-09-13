"""Мера полезности пополнения словарей (SPEC §8.11, agent/reach.py).

Главный вопрос пользователя — «варианты моей корзины в разных магазинах», и он
упирается в покрытие. Здесь проверяется, что мера этого не заслоняет: числа
совпадают с тем, что показывают сами функции ядра, потолок печатается рядом с
достигнутым, а «полезно» означает движение ответа, а не рост процентов.
"""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fixture  # noqa: E402

from agent.adapters.receipts_fns import Pipeline, load_receipts               # noqa: E402
from agent.adapters.receipts_fns import diagnostics as D                      # noqa: E402
from agent.adapters.receipts_fns.pipeline import to_history                   # noqa: E402
from agent.config import Config, dataset_path                                 # noqa: E402
from agent.profile import build, compare, for_period, smart_basket            # noqa: E402
from agent.reach import chain, measure, useful                                # noqa: E402
from agent.settings import Settings                                          # noqa: E402


def run_with(**rules):
    """Прогон с пользовательскими правилами поверх конфигов."""
    config = Config.load(BASE).with_rules(**rules)
    run = Pipeline(config).run(list(fixture.receipts()))
    history = to_history(run)
    return run, history, build(history, Settings())


class ReachTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pipeline = fixture.run()
        cls.history = fixture.history()
        cls.profile = fixture.profile()
        cls.reach = measure(cls.pipeline, cls.history, cls.profile)

    def test_comparable_matches_what_8_6_shows(self):
        """Панель, показывающая «сравнимых 58», пока страница показывает 25,
        хуже отсутствия панели: она врёт так же убедительно."""
        self.assertEqual(self.reach.comparable_shown, len(compare(self.history)))
        self.assertEqual(self.reach.comparable_total,
                         len(compare(self.history, include_blended=True)))

    def test_split_matches_what_the_smart_basket_shows(self):
        basket = for_period(self.profile, "week")
        route = smart_basket(self.history, basket)
        self.assertEqual(self.reach.split_lines, len(route.lines))
        self.assertEqual(self.reach.basket_lines, len(basket.lines))
        self.assertAlmostEqual(self.reach.saving, route.saving_abs)

    def test_blended_pools_are_not_promised_as_one_rule_away(self):
        """Пул — это разные товары, сложенные вместе, а не товар без марки."""
        self.assertEqual(self.reach.blended_pools,
                         self.reach.comparable_total - self.reach.comparable_shown)
        self.assertGreater(self.reach.blended_pools, 0)

    def test_ceiling_is_at_least_what_is_achieved(self):
        """Потолок — строки, у которых в группе есть сравнимый товар."""
        self.assertGreaterEqual(self.reach.ceiling_lines, self.reach.split_lines)
        self.assertLessEqual(self.reach.ceiling_lines, self.reach.basket_lines)

    def test_ceiling_is_above_the_answer_and_that_is_not_coverage(self):
        """Зазор между достигнутым и потолком словарями не лечится (П-7).

        Маршрут берёт у группы один типичный товар; если сравним другой товар той
        же группы, строка не разводится. Если этот тест когда-нибудь сломается,
        значит зазор закрыли — и тогда П-7 закрыт, а не тест устарел.
        """
        self.assertGreater(self.reach.ceiling_lines, self.reach.split_lines)

    def test_shares_have_their_denominators(self):
        self.assertAlmostEqual(self.reach.grouped_share,
                               self.reach.grouped / self.reach.analysable)
        self.assertAlmostEqual(self.reach.branded_share,
                               self.reach.branded / self.reach.unit_items)
        self.assertAlmostEqual(self.reach.split_share,
                               self.reach.split_lines / self.reach.basket_lines)

    def test_chain_is_ordered_by_causality(self):
        steps = chain(self.reach, self.reach)
        names = [s.name for s in steps]
        self.assertLess(names.index("Распознан бренд"),
                        names.index("Сравнимо между магазинами"))
        self.assertLess(names.index("Сравнимо между магазинами"),
                        names.index("Разводится по магазинам"))
        self.assertTrue(any(s.decisive for s in steps))

    def test_chain_against_itself_moves_nothing(self):
        for step in chain(self.reach, self.reach):
            self.assertEqual(step.delta, 0)
            self.assertFalse(step.moved)
        self.assertFalse(useful(self.reach, self.reach))


class MeasuredReplenishmentTest(unittest.TestCase):
    """Эффект правила меряется, а не предсказывается."""

    @classmethod
    def setUpClass(cls):
        cls.before = measure(fixture.run(), fixture.history(), fixture.profile())

    def test_brand_rule_from_the_effect_queue_adds_a_comparable_item(self):
        """Очередь по эффекту обещает проверяемое, и обещание держится.

        «ДСК ОГУРЦЫ КОРОТКОПЛОДНЫЕ 450Г» встречается 11 раз в двух магазинах по
        отдельности, то есть наблюдений уже достаточно и они про один товар.
        Значит разметка марки обязана дать сравнимый товар.
        """
        after = measure(*run_with(brands=[{"brand": "ДСК", "match": "ДСК ОГУРЦЫ"}]))
        self.assertGreater(after.comparable_shown, self.before.comparable_shown)
        self.assertTrue(useful(self.before, after))

    def test_rule_that_unlocks_nothing_is_reported_as_such(self):
        """Правило про редкую марку — не вред, но и не прибавка, и это видно."""
        after = measure(*run_with(brands=[{"brand": "Оленица", "match": "ОЛЕНИЦ"}]))
        self.assertEqual(after.comparable_shown, self.before.comparable_shown)
        self.assertEqual(after.split_lines, self.before.split_lines)
        self.assertFalse(useful(self.before, after))

    def test_category_rule_moves_positions_not_the_main_question(self):
        """П-6: молочный шоколад уезжает из «молока», и главная мера его не видит.

        Это не изъян меры, а её область: правило исправляет отнесение, и его
        эффект измеряется размером группы, а не сравнимостью.
        """
        run, history, profile = run_with(
            categories=[{"group": "vypechka", "match": "ШОК.*МОЛ"}])
        after = measure(run, history, profile)
        self.assertEqual(after.comparable_shown, self.before.comparable_shown)
        self.assertGreater(D.group_size(run, "vypechka"),
                           D.group_size(fixture.run(), "vypechka"))
        self.assertLess(D.group_size(run, "moloko"),
                        D.group_size(fixture.run(), "moloko"))


class BrandQueueTest(unittest.TestCase):
    """Очередь по эффекту против очереди по объёму."""

    @classmethod
    def setUpClass(cls):
        cls.pipeline = fixture.run()
        profile = fixture.profile()
        cls.week = {l.group for l in for_period(profile, "week").lines}
        cls.month = {l.group for l in for_period(profile, "month").lines}
        cls.candidates = D.brand_candidates(cls.pipeline, basket_groups=cls.week,
                                            month_groups=cls.month)

    def test_every_candidate_is_already_comparable_by_itself(self):
        """Обещание очереди проверяемое: имя уже набрало наблюдений в двух местах."""
        minimum = self.pipeline.rules.min_observations
        for candidate in self.candidates:
            self.assertGreaterEqual(candidate.venues, 2, candidate.name)
            self.assertGreaterEqual(candidate.purchases, minimum * 2, candidate.name)

    def test_basket_candidates_come_first(self):
        """Порядок — порядок полезности: строка недельной корзины раньше прочего."""
        flags = [c.in_basket for c in self.candidates]
        self.assertEqual(flags, sorted(flags, reverse=True))

    def test_candidates_are_brandless_and_packaged(self):
        names = {c.name for c in self.candidates}
        for item in self.pipeline.items:
            if item.name in names and item.key is not None:
                self.assertEqual(item.brand, "—", item.name)
                self.assertFalse(item.weighted, item.name)

    def test_volume_queue_is_a_different_question(self):
        """Очередь по объёму честно показывает массу, а не марки."""
        tokens = {token for token, _n in D.brandless_prefixes(self.pipeline, 10)}
        self.assertTrue(tokens)
        self.assertNotEqual(tokens, {c.name for c in self.candidates})


class PositionsTest(unittest.TestCase):
    """Чем определена фасовка у каждой позиции — §8.11 буквально.

    Атрибут назван `pipeline`, а не `run`: `run` — метод самого TestCase, и
    присваивание его молча ломает запуск класса целиком.
    """

    @classmethod
    def setUpClass(cls):
        cls.pipeline = fixture.run()

    def test_every_position_carries_a_reason(self):
        rows, total = D.positions(self.pipeline, limit=500)
        self.assertGreater(total, 10000)
        for row in rows:
            self.assertTrue(row.status)

    def test_pack_sources_cover_every_analysed_position(self):
        sources = dict(D.pack_sources(self.pipeline))
        _rows, total = D.positions(self.pipeline, limit=1)
        self.assertEqual(sum(sources.values()), total)

    def test_filters_narrow_the_answer(self):
        _rows, everything = D.positions(self.pipeline, limit=1)
        _rows, weighted = D.positions(self.pipeline, status="весовой", limit=1)
        _rows, searched = D.positions(self.pipeline, search="огурцы", limit=1)
        self.assertLess(weighted, everything)
        self.assertLess(searched, everything)

    def test_search_folds_yo_like_everything_else(self):
        _rows, with_yo = D.positions(self.pipeline, search="свёкла", limit=1)
        _rows, without = D.positions(self.pipeline, search="свекла", limit=1)
        self.assertEqual(with_yo, without)

    def test_pagination_does_not_repeat_rows(self):
        first, _ = D.positions(self.pipeline, limit=50)
        second, _ = D.positions(self.pipeline, offset=50, limit=50)
        self.assertEqual(len(first), 50)
        self.assertNotEqual([r.raw for r in first], [r.raw for r in second])


if __name__ == "__main__":
    unittest.main()
