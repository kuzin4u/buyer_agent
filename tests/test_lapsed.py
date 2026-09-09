"""8.4 Что давно не покупал (SPEC §8.4)."""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from agent.config import Config, dataset_path                       # noqa: E402
from agent.settings import Settings                                 # noqa: E402
from agent.adapters.receipts_fns import Pipeline, load_receipts     # noqa: E402
from agent.adapters.receipts_fns.pipeline import to_history         # noqa: E402
from agent.profile import build, lapsed, to_restock                 # noqa: E402
from agent.profile.lapsed import ABANDONED, OVERDUE                 # noqa: E402


class LapsedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run = Pipeline(Config.load(BASE)).run(load_receipts(dataset_path(BASE)))
        cls.profile = build(to_history(run), Settings())

    def test_sorted_by_how_overdue(self):
        result = lapsed(self.profile)
        self.assertTrue(result)
        overdues = [x.overdue for x in result]
        self.assertEqual(overdues, sorted(overdues, reverse=True))

    def test_defaults_to_last_known_date(self):
        """Для статичного датасета «сейчас» — конец истории, а не сегодня."""
        result = lapsed(self.profile)
        self.assertLessEqual(max(x.days_since for x in result), 400)

    def test_overdue_is_relative_to_the_groups_own_rhythm(self):
        """Просрочка меряется своим интервалом группы, а не общим числом дней."""
        top = lapsed(self.profile)[0]
        self.assertAlmostEqual(top.overdue,
                               top.days_since / top.median_gap_days, places=6)

    def test_threshold_filters(self):
        loose = lapsed(self.profile, min_overdue=1.0)
        strict = lapsed(self.profile, min_overdue=5.0)
        self.assertGreater(len(loose), len(strict))
        self.assertTrue(all(x.overdue >= 5.0 for x in strict))

    def test_asof_moves_the_horizon(self):
        later = lapsed(self.profile, asof="2027-06-01")
        self.assertGreater(len(later), len(lapsed(self.profile)))

    def test_abandoned_is_not_offered_for_restock(self):
        """Оставленная привычка — не просрочка; «докупить» тут неуместно."""
        later = lapsed(self.profile, asof="2027-06-01")
        verdicts = {x.verdict for x in later}
        self.assertIn(ABANDONED, verdicts)
        restock = to_restock(self.profile, asof="2027-06-01")
        self.assertTrue(all(x.verdict == OVERDUE for x in restock))
        self.assertLess(len(restock), len(later))

    def test_only_staples_by_default(self):
        """Раз в квартал купленное не может «давно не покупаться»."""
        groups = {x.group for x in lapsed(self.profile, min_overdue=1.0)}
        self.assertFalse(groups - set(self.profile.staples))

    def test_carries_what_to_buy_and_for_how_much(self):
        for item in lapsed(self.profile):
            self.assertTrue(item.label)
            self.assertGreater(item.expected_amount, 0)

    def test_empty_profile_is_not_an_error(self):
        from agent.history import History
        empty = build(History(principal="никто"), Settings())
        self.assertEqual(lapsed(empty), [])


if __name__ == "__main__":
    unittest.main()
