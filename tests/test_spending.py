"""8.8 Контроль трат (SPEC §8.8).

Главное требование к этой функции — полнота охвата: «сюда входит non_food,
это тоже расходы». Половина тестов про то, что деньги не теряются.
"""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from agent.config import Config, dataset_path                       # noqa: E402
from agent.adapters.receipts_fns import Pipeline, load_receipts     # noqa: E402
from agent.adapters.receipts_fns.pipeline import to_history         # noqa: E402
from agent.profile import breakdown, growth, monthly_series, summary  # noqa: E402


class SpendingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run = Pipeline(Config.load(BASE)).run(load_receipts(dataset_path(BASE)))
        cls.history = to_history(run)
        cls.total = sum(o.amount for o in cls.history.outlays)

    def test_every_dimension_accounts_for_all_the_money(self):
        """Ни один разрез не имеет права терять рубли."""
        for dimension in ("year", "month", "venue", "dept", "group", "segment"):
            got = sum(b.amount for b in breakdown(self.history, dimension))
            self.assertAlmostEqual(got, self.total, places=2, msg=dimension)

    def test_non_food_is_counted(self):
        """§8.8 прямо требует включить непродуктовое."""
        segments = {b.key: b.amount for b in breakdown(self.history, "segment")}
        self.assertGreater(segments["non_food"], 900_000)
        self.assertGreater(segments["unknown"], 700_000)

    def test_unclassified_money_is_not_one_bucket(self):
        """Аптека и нераспознанный творог — не одно и то же."""
        keys = {b.key for b in breakdown(self.history, "group")}
        self.assertIn("непродуктовое", keys)
        self.assertIn("контрагент не установлен", keys)
        self.assertIn("не отнесено к группе", keys)

    def test_shares_sum_to_one(self):
        self.assertAlmostEqual(
            sum(b.share for b in breakdown(self.history, "year")), 1.0, places=6)

    def test_unknown_dimension_is_rejected(self):
        with self.assertRaises(ValueError):
            breakdown(self.history, "колхоз")
        with self.assertRaises(ValueError):
            growth(self.history, "колхоз")

    def test_growth_compares_windows_not_calendar_years(self):
        """Последний год в истории неполный: календарное сравнение врало бы."""
        trends = {t.key: t for t in growth(self.history, "venue")}
        globus = trends["Глобус"]
        self.assertAlmostEqual(globus.delta_abs, globus.after - globus.before,
                               places=6)
        year = {b.key: b.amount for b in breakdown(self.history, "year")}
        self.assertNotAlmostEqual(globus.after, year["2026"], places=0)

    def test_growth_is_ranked_by_roubles_not_percent(self):
        """§8.5: приоритетная мера — абсолютный рост, процент вспомогательный."""
        deltas = [abs(t.delta_abs) for t in growth(self.history, "group")]
        self.assertEqual(deltas, sorted(deltas, reverse=True))

    def test_growth_finds_both_directions(self):
        trends = growth(self.history, "group")
        self.assertTrue([t for t in trends if t.delta_abs > 0])
        self.assertTrue([t for t in trends if t.delta_abs < 0])
        for trend in trends:
            self.assertIn(trend.direction, ("рост", "снижение", "без изменений"))

    def test_monthly_series_is_ordered_and_complete(self):
        series = monthly_series(self.history)
        self.assertEqual([m for m, _a in series], sorted(m for m, _a in series))
        self.assertAlmostEqual(sum(a for _m, a in series), self.total, places=2)

    def test_summary(self):
        s = summary(self.history)
        self.assertAlmostEqual(s["total"], self.total, places=2)
        self.assertEqual(s["first_month"], "2017-06")
        self.assertEqual(s["last_month"], "2026-09")
        self.assertGreater(s["median_month"], 0)

    def test_segment_filter_narrows_the_scope(self):
        food = sum(b.amount for b in breakdown(self.history, "year",
                                               segments=("food_core",)))
        self.assertLess(food, self.total)
        self.assertAlmostEqual(food, 3_693_737, delta=10)


if __name__ == "__main__":
    unittest.main()
