"""8.5 Динамика цен (SPEC §8.5, docs/DECISIONS.md Р-21).

Проверяется не «функция что-то вернула», а три обещания: сравнение идёт
внутри ключа, порядок задаёт абсолютный рубль, и ряды, где число выглядит
динамикой цены, но ею не является, в ответ по умолчанию не попадают.
"""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fixture  # noqa: E402

from agent.history import Event, History, ItemKey, UNKNOWN   # noqa: E402
from agent.profile import prices                             # noqa: E402


def _key(group="syr", brand="Viola", label=None, kind="unit", unit="kg"):
    parts = (brand, "—", "0.2кг") if kind == "unit" else (label or "ТОВАР",)
    return ItemKey(kind=kind, group=group, parts=parts, unit=unit,
                   label=label or " · ".join(parts))


def _history(rows):
    """rows: (год, ключ, цена, количество) → History."""
    events = [Event(ts=f"{y}-06-15T12:00:00", key=k, qty=q, unit_price=p,
                    amount=p * q, venue="Глобус")
              for y, k, p, q in rows]
    return History(principal="test", events=events)


class SeriesTest(unittest.TestCase):
    def test_year_below_threshold_is_dropped(self):
        """Одна покупка по акции не должна выглядеть падением цены."""
        k = _key()
        h = _history([(2020, k, 100, 1)] * 3 + [(2021, k, 40, 1)] * 2)
        row = prices.series(h, k)
        self.assertEqual([y.year for y in row], ["2020"])

    def test_median_not_mean(self):
        k = _key()
        h = _history([(2020, k, 100, 1), (2020, k, 110, 1), (2020, k, 900, 1)])
        self.assertEqual(prices.series(h, k)[0].median, 110)


class DynamicsTest(unittest.TestCase):
    def test_order_is_absolute_rouble_not_percent(self):
        """Рост с 20 до 80 ₽ — это +300%, но ниже роста с 900 до 1035 ₽."""
        cheap = _key(brand="Дешёвый", label="дешёвый")
        dear = _key(brand="Дорогой", label="дорогой")
        h = _history([(2020, cheap, 20, 1)] * 3 + [(2021, cheap, 80, 1)] * 3 +
                     [(2020, dear, 900, 1)] * 3 + [(2021, dear, 1035, 1)] * 3)
        order = [t.key.label for t in prices.dynamics(h)]
        self.assertEqual(order, ["дорогой", "дешёвый"])
        by_label = {t.key.label: t for t in prices.dynamics(h)}
        self.assertAlmostEqual(by_label["дешёвый"].delta_pct, 3.0)
        self.assertAlmostEqual(by_label["дорогой"].delta_pct, 0.15)

    def test_blended_key_is_excluded_by_default(self):
        """«Сыр 0,2 кг без марки» — это корзина разных сыров, а не товар."""
        blended = _key(brand=UNKNOWN, label="без марки")
        named = _key(brand="Viola", label="с маркой")
        h = _history([(2020, blended, 100, 1)] * 3 + [(2021, blended, 400, 1)] * 3 +
                     [(2020, named, 100, 1)] * 3 + [(2021, named, 150, 1)] * 3)
        self.assertEqual([t.key.label for t in prices.dynamics(h)], ["с маркой"])
        with_blended = prices.dynamics(h, include_blended=True)
        self.assertEqual(len(with_blended), 2)
        self.assertTrue(with_blended[0].blended)

    def test_bulk_key_is_not_blended(self):
        """У весового ключа различитель — название товара, а не марка (Р-3)."""
        bulk = _key(kind="bulk", label="БАНАНЫ", group="frukty")
        h = _history([(2020, bulk, 60, 1.2)] * 3 + [(2021, bulk, 90, 1.1)] * 3)
        self.assertFalse(bulk.blended)
        self.assertEqual(len(prices.dynamics(h)), 1)

    def test_changed_selling_mode_is_excluded_by_default(self):
        """На вес в одном году, поштучно в другом — это не динамика цены."""
        k = _key(kind="bulk", label="КОТЛЕТЫ", group="polufabr")
        h = _history([(2017, k, 369, 0.94), (2017, k, 369, 0.65), (2017, k, 339, 1.04)] +
                     [(2024, k, 77, 1.0)] * 3)
        self.assertEqual(prices.dynamics(h), [])
        loose = prices.dynamics(h, include_mode_changed=True)
        self.assertEqual(len(loose), 1)
        self.assertTrue(loose[0].mode_changed)

    def test_short_series_is_not_a_trend(self):
        k = _key()
        h = _history([(2020, k, 100, 1)] * 3)
        self.assertEqual(prices.dynamics(h), [])

    def test_real_dataset_keeps_units_inside_one_trend(self):
        """Внутри ряда единица одна: ₽/кг с ₽/шт не сравниваются."""
        for t in prices.dynamics(fixture.history()):
            self.assertEqual(t.unit, t.key.unit)
            self.assertIn(t.unit, ("kg", "l", "pcs"))

    def test_real_dataset_excludes_the_known_artefacts(self):
        h = fixture.history()
        clean = prices.dynamics(h)
        everything = prices.dynamics(h, include_blended=True,
                                     include_mode_changed=True)
        self.assertLess(len(clean), len(everything))
        self.assertFalse([t for t in clean if t.blended or t.mode_changed])
        self.assertTrue([t for t in everything if t.blended])
        self.assertTrue([t for t in everything if t.mode_changed])


class ProfileScopeTest(unittest.TestCase):
    def test_for_profile_looks_only_at_staples(self):
        prof = fixture.profile()
        h = fixture.history()
        staples = set(prof.staples)
        for t in prices.for_profile(prof, h):
            self.assertIn(t.key.group, staples)
        self.assertLessEqual(len(prices.for_profile(prof, h)),
                             len(prices.dynamics(h)))


class SummaryTest(unittest.TestCase):
    def test_summary_does_not_add_up_different_units(self):
        """Сумма ₽/кг и ₽/шт правдоподобна и бессмысленна — её здесь нет."""
        s = prices.summary(prices.dynamics(fixture.history()))
        self.assertNotIn("total_abs", s)
        self.assertEqual(s["keys"], s["up"] + s["down"] + s["flat"])

    def test_empty_input_does_not_raise(self):
        s = prices.summary([])
        self.assertEqual(s["keys"], 0)
        self.assertEqual(s["span"], (None, None))


if __name__ == "__main__":
    unittest.main()
