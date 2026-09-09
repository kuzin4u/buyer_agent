"""Две очереди на пополнение — и они разные (SPEC §6, §8.11).

Задача А: отнести непродуктовое в свои категории. Мера рублёвая, цель —
корректный контроль трат. Задача Б: пополнить продуктовые группы. Мера в
наблюдениях, цель — покрытие для медиан.

Смешивать нельзя: они лечатся разным и меряются разным. Одна очередь по числу
покупок не увидит гриль за 17 тыс ₽, одна по деньгам не увидит творожок
за 39 ₽, купленный двенадцать раз.
"""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fixture  # noqa: E402

from agent.adapters.receipts_fns.diagnostics import (               # noqa: E402
    coverage, queue_b_size, queue_by_money, queue_by_purchases)


class QueueTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = fixture.run()
        cls.cov = coverage(cls.result)
        cls.min_obs = cls.result.rules.min_observations

    def test_uncategorised_money_is_measured(self):
        self.assertAlmostEqual(self.cov.unmatched_total, 1_212_443, delta=1)
        self.assertGreater(self.cov.unmatched_share, 0.3)

    def test_queue_a_is_ranked_by_money(self):
        rows = queue_by_money(self.cov, 20)
        amounts = [r.amount for r in rows]
        self.assertEqual(amounts, sorted(amounts, reverse=True))

    def test_queue_b_is_ranked_by_purchases(self):
        rows = queue_by_purchases(self.cov, self.min_obs, 20)
        counts = [r.purchases for r in rows]
        self.assertEqual(counts, sorted(counts, reverse=True))

    def test_queue_b_respects_the_median_threshold(self):
        """Ниже порога наблюдений медиану не построить — разбирать нечего."""
        rows = queue_by_purchases(self.cov, self.min_obs, 500)
        self.assertTrue(all(r.purchases >= self.min_obs for r in rows))
        count, amount = queue_b_size(self.cov, self.min_obs)
        self.assertEqual(count, 220)
        self.assertAlmostEqual(amount, 314_687, delta=1)

    def test_queue_a_sees_what_queue_b_cannot(self):
        """Разовая дорогая покупка — вся суть очереди А."""
        a_names = {r.name for r in queue_by_money(self.cov, 10)}
        b_names = {r.name for r in queue_by_purchases(self.cov, self.min_obs, 500)}
        one_off = {r.name for r in queue_by_money(self.cov, 10) if r.purchases == 1}
        self.assertTrue(one_off)
        self.assertFalse(one_off & b_names)
        self.assertIn("КУХОННАЯ МАШИНА", a_names)

    def test_queue_b_sees_what_queue_a_cannot(self):
        """Дешёвое и частое не попадёт в денежную очередь никогда."""
        b_top = queue_by_purchases(self.cov, self.min_obs, 5)
        a_names = {r.name for r in queue_by_money(self.cov, 50)}
        cheap = [r for r in b_top if r.amount < 2000]
        self.assertTrue(cheap)
        self.assertFalse({r.name for r in cheap} & a_names)

    def test_frequency_is_order_not_verdict(self):
        """Регулярно покупают и расходники: частота не делает позицию едой."""
        rows = {r.name: r for r in queue_by_purchases(self.cov, self.min_obs, 500)}
        self.assertIn("МАСЛО TAIF 5W40", rows)
        self.assertGreaterEqual(rows["МАСЛО TAIF 5W40"].purchases, 9)

    def test_money_accounting_adds_up(self):
        analysable = self.cov.matched_money + self.cov.unmatched_total
        self.assertAlmostEqual(self.cov.money, analysable, places=6)
        self.assertGreater(self.cov.excluded_money, 0)


if __name__ == "__main__":
    unittest.main()
