"""8.2 Корзина «как обычно» (SPEC §8.2)."""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fixture  # noqa: E402

from agent.config import Config, dataset_path                       # noqa: E402
from agent.settings import Settings                                 # noqa: E402
from agent.adapters.receipts_fns import Pipeline, load_receipts     # noqa: E402
from agent.adapters.receipts_fns.pipeline import to_history         # noqa: E402
from agent.profile import build, for_average_txn, for_period        # noqa: E402


class BasketTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.history = fixture.history()
        cls.settings = Settings()
        cls.profile = fixture.profile()

    def test_week_is_smaller_than_month(self):
        week = for_period(self.profile, "week", self.settings)
        month = for_period(self.profile, "month", self.settings)
        self.assertLess(len(week), len(month))
        self.assertLess(week.total, month.total)

    def test_week_keeps_only_what_is_actually_weekly(self):
        """Группа, которую берут раз в месяц, в недельную корзину не идёт."""
        week = for_period(self.profile, "week", self.settings)
        groups = {line.group for line in week.lines}
        self.assertIn("moloko", groups)
        self.assertNotIn("chay_kofe", groups)

    def test_unknown_period_is_rejected(self):
        with self.assertRaises(ValueError):
            for_period(self.profile, "quarter", self.settings)

    def test_average_basket_fits_the_average_txn(self):
        basket = for_average_txn(self.profile, settings=self.settings)
        self.assertLessEqual(basket.total, self.profile.avg_txn)
        self.assertGreater(basket.total, self.profile.avg_txn * 0.8)

    def test_explicit_budget_is_respected(self):
        basket = for_average_txn(self.profile, budget=1000, settings=self.settings)
        self.assertLessEqual(basket.total, 1000)

    def test_every_line_carries_its_typical_key(self):
        """§4: берётся группа, подставляется самый частый ключ внутри неё."""
        for line in for_period(self.profile, "month", self.settings).lines:
            if line.typical_key is None:
                continue
            self.assertEqual(line.typical_key.group, line.group)
            self.assertTrue(line.label)

    def test_required_group_enters_any_basket(self):
        """SPEC §3.3: обязательная позиция попадает, даже если редкая."""
        settings = Settings(required_groups=("konservy",))
        profile = build(self.history, settings)
        week = for_period(profile, "week", settings)
        line = next(x for x in week.lines if x.group == "konservy")
        self.assertEqual(line.reason, "обязательная")

    def test_excluded_group_never_appears(self):
        settings = Settings(excluded_groups=("pivo", "vino", "krepkiy"))
        profile = build(self.history, settings)
        for basket in (for_period(profile, "month", settings),
                       for_average_txn(profile, settings=settings)):
            groups = {line.group for line in basket.lines}
            self.assertFalse(groups & {"pivo", "vino", "krepkiy"})

    def test_grouping_by_dept(self):
        month = for_period(self.profile, "month", self.settings)
        by_dept = month.by_dept()
        self.assertIn("Молочное", by_dept)
        self.assertEqual(sum(len(v) for v in by_dept.values()), len(month))


if __name__ == "__main__":
    unittest.main()
