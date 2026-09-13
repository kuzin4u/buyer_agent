"""8.6 Сравнение площадок и 8.7 выбор площадки (SPEC §8.6–8.7, Р-21).

Главное, что здесь проверяется, — не арифметика экономии, а то, что она не
надувается: строка, которую не с чем сравнить, в экономию не засчитывается, а
площадка без достаточного числа сравнимых товаров не получает ранга вовсе.
"""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fixture  # noqa: E402

from agent.history import Event, History, ItemKey, UNKNOWN   # noqa: E402
from agent.settings import Settings                          # noqa: E402
from agent.profile import venues                             # noqa: E402
from agent.profile.basket import Basket, BasketLine          # noqa: E402


def _key(label, group="syr", brand="Viola", unit="kg"):
    return ItemKey(kind="unit", group=group, parts=(brand, "—", "0.2кг"),
                   unit=unit, label=label)


def _history(rows):
    """rows: (площадка, ключ, цена, сколько раз) → History."""
    events = []
    for venue, key, price, times in rows:
        events += [Event(ts="2024-06-15T12:00:00", key=key, qty=1.0,
                         unit_price=price, amount=price, venue=venue)
                   for _ in range(times)]
    return History(principal="test", events=events)


def _line(key, amount, unit_price):
    return BasketLine(group=key.group, dept=None, times=1.0, qty=1,
                      amount=amount, typical_key=key, unit_price=unit_price,
                      reason="регулярная")


class CompareTest(unittest.TestCase):
    def test_needs_three_purchases_at_two_venues(self):
        k = _key("сыр")
        self.assertEqual(venues.compare(_history([("А", k, 100, 3),
                                                  ("Б", k, 120, 2)])), [])
        rows = venues.compare(_history([("А", k, 100, 3), ("Б", k, 120, 3)]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].cheapest.venue, "А")
        self.assertEqual(rows[0].dearest.venue, "Б")
        self.assertEqual(rows[0].spread_abs, 20)
        self.assertAlmostEqual(rows[0].spread_pct, 0.2)

    def test_one_venue_is_not_a_comparison(self):
        k = _key("сыр")
        self.assertEqual(venues.compare(_history([("А", k, 100, 9)])), [])

    def test_blended_key_is_excluded_by_default(self):
        """«Заморозка 0,08 кг» у двух магазинов — это разные товары."""
        blended = _key("без марки", brand=UNKNOWN)
        h = _history([("А", blended, 531, 3), ("Б", blended, 2000, 3)])
        self.assertEqual(venues.compare(h), [])
        self.assertEqual(len(venues.compare(h, include_blended=True)), 1)

    def test_order_is_absolute_spread(self):
        big = _key("дорогой разрыв")
        small = _key("большой процент", brand="Другой")
        h = _history([("А", big, 900, 3), ("Б", big, 1100, 3),
                      ("А", small, 10, 3), ("Б", small, 40, 3)])
        self.assertEqual([c.key.label for c in venues.compare(h)],
                         ["дорогой разрыв", "большой процент"])


class SmartBasketTest(unittest.TestCase):
    def setUp(self):
        self.cheese = _key("сыр")
        self.milk = _key("молоко", group="moloko", brand="Му-у", unit="l")
        self.rare = _key("редкое", group="krupa", brand="Редкий")
        self.history = _history([
            ("А", self.cheese, 100, 3), ("Б", self.cheese, 200, 3),
            ("А", self.milk, 80, 3), ("Б", self.milk, 50, 3),
            ("А", self.rare, 300, 3),            # только в одном магазине
        ])

    def _basket(self, lines):
        return Basket(mode="test", period="week", lines=tuple(lines),
                      total=sum(l.amount for l in lines))

    def test_saving_is_against_the_best_single_venue(self):
        """Вопрос пользователя — «ехать в два места или в одно»."""
        b = self._basket([_line(self.cheese, 100, 100), _line(self.milk, 80, 80)])
        route = venues.smart_basket(self.history, b)
        # в «А» 100 + 80 = 180, в «Б» 200 + 50 = 250 → лучшая одиночная «А»
        self.assertEqual(route.best_single, "А")
        self.assertAlmostEqual(route.baseline_total, 180)
        # врозь: сыр в «А» за 100, молоко в «Б» за 50
        self.assertAlmostEqual(route.split_total, 150)
        self.assertAlmostEqual(route.saving_abs, 30)
        self.assertEqual(route.venues, ["А", "Б"])

    def test_incomparable_line_is_skipped_not_counted(self):
        """Иначе достаточно не знать цену у дорогого магазина, чтобы «сэкономить»."""
        b = self._basket([_line(self.cheese, 100, 100), _line(self.rare, 300, 300)])
        route = venues.smart_basket(self.history, b)
        self.assertEqual([l.typical_key.label for l in route.skipped], ["редкое"])
        self.assertEqual(len(route.lines), 1)
        self.assertAlmostEqual(route.covered, 0.5)
        self.assertNotIn(300, [l.cost for l in route.lines])

    def test_coverage_is_reported_so_saving_can_be_judged(self):
        b = self._basket([_line(self.rare, 300, 300)])
        route = venues.smart_basket(self.history, b)
        self.assertEqual(route.lines, ())
        self.assertEqual(route.covered, 0.0)
        self.assertEqual(route.saving_abs, 0.0)
        self.assertIsNone(route.best_single)

    def test_real_basket_saving_never_exceeds_baseline(self):
        history, prof = fixture.history(), fixture.profile()
        from agent.profile import basket
        route = venues.smart_basket(history, basket.for_period(prof, "week"))
        self.assertGreaterEqual(route.saving_abs, 0)
        self.assertLessEqual(route.split_total, route.baseline_total)
        self.assertTrue(0 <= route.covered <= 1)


class RankTest(unittest.TestCase):
    def _rich_history(self):
        rows = []
        for i in range(6):
            k = _key(f"товар {i}", brand=f"Марка{i}")
            rows += [("Дёшево", k, 80, 3), ("Дорого", k, 120, 3),
                     ("Редкий", k, 50, 3) if i == 0 else ("Дорого", k, 120, 0)]
        return _history([r for r in rows if r[3]])

    def test_venue_with_too_few_keys_gets_no_rank(self):
        """По одному совпавшему товару «дешевле» звучит так же уверенно."""
        ranked = venues.rank(self._rich_history())
        self.assertEqual({s.venue for s in ranked}, {"Дёшево", "Дорого"})

    def test_cheapest_venue_scores_one(self):
        ranked = venues.rank(self._rich_history())
        self.assertEqual(ranked[0].venue, "Дёшево")
        self.assertAlmostEqual(ranked[0].price_score, 1.0)
        self.assertAlmostEqual(ranked[-1].price_score, 0.0)

    def test_without_ratings_total_is_price_only(self):
        for s in venues.rank(self._rich_history()):
            self.assertFalse(s.rated)
            self.assertEqual(s.basis, ("цена",))
            self.assertAlmostEqual(s.total, s.price_score)

    def test_partial_ratings_do_not_enter_the_total(self):
        """Оценена одна площадка из двух — сравнивать по оценкам нельзя.

        Иначе выставленные пользователем пять звёзд опускали бы оценённую
        площадку ниже неоценённой: у той в итоге осталась бы одна цена.
        """
        settings = Settings(venue_ratings={"Дорого": {"quality": 5, "ambience": 5}})
        ranked = {s.venue: s for s in venues.rank(self._rich_history(), settings)}
        dear, cheap = ranked["Дорого"], ranked["Дёшево"]
        self.assertEqual(dear.quality, 5)          # оценка видна
        self.assertEqual(dear.basis, ("цена",))    # но в итог не вошла
        self.assertAlmostEqual(dear.total, dear.price_score)
        self.assertGreater(cheap.total, dear.total)

    def test_ratings_count_when_every_venue_has_them(self):
        """Качество и обстановку ставит человек: в чеках их нет (§8.7)."""
        settings = Settings(venue_ratings={
            "Дорого": {"quality": 5, "ambience": 5},
            "Дёшево": {"quality": 1, "ambience": 1}})
        ranked = {s.venue: s for s in venues.rank(self._rich_history(), settings)}
        dear, cheap = ranked["Дорого"], ranked["Дёшево"]
        self.assertEqual(dear.basis, ("цена", "качество", "обстановка"))
        # дорогой: цена 0, качество 1, обстановка 1 → 2/3
        self.assertAlmostEqual(dear.total, 2 / 3)
        # дешёвый: цена 1, качество 0, обстановка 0 → 1/3
        self.assertAlmostEqual(cheap.total, 1 / 3)
        self.assertGreater(dear.total, cheap.total)

    def test_price_index_is_a_ratio_not_a_sum(self):
        """Складывать ₽/кг с ₽/шт нельзя, отношение внутри ключа — можно."""
        index = venues.price_index(self._rich_history())
        self.assertLess(index["Дёшево"][0], 1.0)
        self.assertGreater(index["Дорого"][0], 1.0)

    def test_real_dataset_ranks_only_well_covered_venues(self):
        ranked = venues.rank(fixture.history())
        self.assertTrue(ranked)
        for s in ranked:
            self.assertGreaterEqual(s.keys, venues.MIN_KEYS_FOR_RANK)
            self.assertTrue(0.0 <= s.price_score <= 1.0)


if __name__ == "__main__":
    unittest.main()
