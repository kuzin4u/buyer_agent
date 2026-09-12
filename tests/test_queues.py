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


def _fake_coverage(names):
    """Покрытие с заданными названиями: {название: (покупок, рублей)}."""
    cov = coverage(fixture.run())
    cov.unmatched.clear()
    cov.unmatched_money.clear()
    for name, (purchases, money) in names.items():
        cov.unmatched[name] = purchases
        cov.unmatched_money[name] = money
    return cov


class QueueTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = fixture.run()
        cls.cov = coverage(cls.result)
        cls.min_obs = cls.result.rules.min_observations

    def test_uncategorised_money_is_measured(self):
        self.assertAlmostEqual(self.cov.unmatched_total, 468_705, delta=1)
        self.assertLess(self.cov.unmatched_share, 0.25)

    def test_one_denominator_for_every_share(self):
        """Р-13: доли считаются от анализируемых, знаменатель один и печатается."""
        self.assertAlmostEqual(self.cov.money, 3_665_593, delta=1)
        self.assertAlmostEqual(
            self.cov.money,
            self.cov.matched_money + self.cov.nonfood_money + self.cov.unmatched_total,
            places=6)
        self.assertAlmostEqual(self.cov.unmatched_share,
                               self.cov.unmatched_total / self.cov.money, places=9)
        # исключённые в знаменатель не входят: они не могут получить группу
        self.assertGreater(self.cov.excluded_money, 0)
        self.assertNotAlmostEqual(self.cov.money,
                                  self.cov.money + self.cov.excluded_money, places=0)

    def test_report_prints_the_denominator(self):
        from agent.adapters.receipts_fns.report import render
        text = render(self.result)
        self.assertIn("анализируется: 14782 позиций, 3 665 593 ₽", text)
        self.assertIn("из 3 665 593 ₽ (12.8% от анализируемых)", text)

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

    def test_queue_b_is_empty_on_this_dataset(self):
        """Задача Б закрыта: разбирать выше порога больше нечего (Р-20).

        Ноль здесь — не «очередь сломалась», а состояние словаря. Что сам
        механизм очереди жив, проверяют тесты ниже: они строят покрытие
        вручную и смотрят, что и как в очередь попадает.
        """
        count, amount = queue_b_size(self.cov, self.min_obs)
        self.assertEqual(count, 0)
        self.assertEqual(round(amount), 0)
        self.assertTrue(self.cov.unmatched, "неразобранные названия всё ещё есть")
        self.assertTrue(all(c < self.min_obs for c in self.cov.unmatched.values()),
                        "выше порога не осталось ни одного названия")

    def test_queue_a_sees_what_queue_b_cannot(self):
        """Разовая дорогая покупка — вся суть очереди А."""
        a_names = {r.name for r in queue_by_money(self.cov, 10)}
        b_names = {r.name for r in queue_by_purchases(self.cov, self.min_obs, 500)}
        one_off = {r.name for r in queue_by_money(self.cov, 10) if r.purchases == 1}
        self.assertTrue(one_off)
        self.assertFalse(one_off & b_names)
        self.assertTrue(a_names)

    def test_queue_b_sees_what_queue_a_cannot(self):
        """Дешёвое и частое не попадёт в денежную очередь никогда.

        На этом датасете очередь Б пуста, поэтому свойство проверяется на
        покрытии, собранном вручную: иначе тест молча превратился бы в
        проверку пустого списка (Р-20).
        """
        cov = _fake_coverage({
            "ТВОРОЖОК ЗА 39": (12, 468.0),      # дёшево и часто — только Б
            "ГРИЛЬ ЗА 17 ТЫСЯЧ": (1, 17_000.0),  # дорого и разово — только А
        })
        b_names = {r.name for r in queue_by_purchases(cov, 3, 5)}
        a_names = {r.name for r in queue_by_money(cov, 1)}
        self.assertEqual(b_names, {"ТВОРОЖОК ЗА 39"})
        self.assertEqual(a_names, {"ГРИЛЬ ЗА 17 ТЫСЯЧ"})
        self.assertFalse(b_names & a_names)

    def test_frequency_is_order_not_verdict(self):
        """Регулярно покупают и расходники: частота не делает позицию едой.

        «КАРТОФ ЕГИП ВЕС» стоял здесь примером оставшегося в очереди Б; в С3
        он разобран и уехал в овощи. Пример заменён на живой остаток очереди.
        """
        rows = {r.name: r for r in queue_by_purchases(self.cov, self.min_obs, 500)}
        self.assertNotIn("МАСЛО TAIF 5W40", rows)   # уехало в avto (задача А)
        self.assertNotIn("КАРТОФ ЕГИП ВЕС", rows)   # уехало в овощи (задача Б)
        self.assertNotIn("САЛФ ВЛ ГЛ МИН 72ШТ", rows)  # уехало в бытовое (Р-19)
        self.assertNotIn("ЧЕРН Б/К ЭКОН 500Г", rows)   # уехало в сухофрукты (Р-20)
        self.assertFalse(rows, "очередь Б разобрана полностью")

    def test_nonfood_does_not_inflate_food_coverage(self):
        """Задача А не должна прятать дыру, которую меряет задача Б."""
        self.assertEqual(self.cov.matched_all, self.cov.matched + self.cov.nonfood)
        self.assertGreater(self.cov.nonfood_money, 500_000)

    def test_money_accounting_adds_up(self):
        analysable = (self.cov.matched_money + self.cov.nonfood_money
                      + self.cov.unmatched_total)
        self.assertAlmostEqual(self.cov.money, analysable, places=6)
        self.assertGreater(self.cov.excluded_money, 0)


if __name__ == "__main__":
    unittest.main()
