"""8.1 Профиль предпочтений (SPEC §8.1).

Профиль строится из History и Settings. Проверяется и то, что он считает
правильно, и то, что он не знает лишнего: слов «чек», «SKU» и «магазин» в его
сигнатуре нет (SPEC §8.10).
"""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from agent.config import Config, dataset_path                       # noqa: E402
from agent.settings import Settings                                 # noqa: E402
from agent.adapters.receipts_fns import Pipeline, load_receipts     # noqa: E402
from agent.adapters.receipts_fns.pipeline import to_history         # noqa: E402
from agent.profile import build                                     # noqa: E402


class ProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run = Pipeline(Config.load(BASE)).run(load_receipts(dataset_path(BASE)))
        cls.history = to_history(run)
        cls.profile = build(cls.history, Settings())

    def test_span_covers_the_dataset(self):
        start, end = self.profile.span
        self.assertTrue(start.startswith("2017-06-07"))
        self.assertTrue(end.startswith("2026-09-05"))
        self.assertGreater(self.profile.months, 100)

    def test_main_venue_reproduces_documented_share(self):
        """DATASET.md: основной магазин даёт 82% продуктовых трат."""
        self.assertEqual(self.profile.main_venue, "Глобус")
        self.assertAlmostEqual(self.profile.main_venue_share, 0.82, places=2)

    def test_candidate_tier_is_off_by_default(self):
        """SPEC §7: food_candidate включается флагом, а не сам собой."""
        self.assertEqual(len(self.profile.venues), 10)
        with_candidates = build(self.history, Settings(include_candidates=True))
        self.assertEqual(len(with_candidates.venues), 20)

    def test_unestablished_venue_never_ranks(self):
        """Покупка без установленной площадки не может быть «основным магазином»."""
        self.assertNotIn(None, self.profile.venues)
        self.assertNotIn("Не определён", self.profile.venues)

    def test_staples_are_the_obvious_ones(self):
        for group in ("moloko", "frukty", "ovoshchi", "smetana", "yaytso"):
            self.assertIn(group, self.profile.staples, group)

    def test_rare_groups_stay_out(self):
        """Раз в квартал — это не постоянная корзина."""
        for group in ("konservy", "yogurt", "slivki", "syrok"):
            self.assertNotIn(group, self.profile.staples, group)
        self.assertLess(self.profile.groups["konservy"].per_month, 1.0)

    def test_required_group_enters_even_if_rare(self):
        """SPEC §8.1: постоянная корзина — с учётом обязательных позиций."""
        rare = "konservy"      # 0,18 раза в месяц — сам по себе не попадёт
        self.assertNotIn(rare, self.profile.staples)
        forced = build(self.history, Settings(required_groups=(rare,)))
        self.assertIn(rare, forced.staples)

    def test_excluded_group_leaves_the_basket(self):
        trimmed = build(self.history, Settings(excluded_groups=("pivo", "vino")))
        self.assertNotIn("pivo", trimmed.staples)
        self.assertNotIn("vino", trimmed.staples)

    def test_typical_key_is_inside_its_group(self):
        """§8.2 подставляет самый частый ключ внутри группы."""
        smetana = self.profile.groups["smetana"]
        self.assertIsNotNone(smetana.typical_key)
        self.assertEqual(smetana.typical_key.group, "smetana")
        self.assertEqual(smetana.typical_key.label, "— · 25% · 0.3кг")

    def test_frequency_and_interval_are_consistent(self):
        """Часто покупаемая группа не может иметь длинный типичный интервал."""
        for stat in self.profile.ranked()[:5]:
            self.assertGreater(stat.per_month, 1.0)
            self.assertIsNotNone(stat.median_gap_days)
            self.assertLess(stat.median_gap_days, 31)

    def test_shares_are_fractions(self):
        self.assertAlmostEqual(sum(self.profile.segment_shares.values()), 1.0, places=6)
        for stat in self.profile.groups.values():
            self.assertGreaterEqual(stat.share, 0.0)
            self.assertLessEqual(stat.share, 1.0)

    def test_profile_needs_no_receipt_vocabulary(self):
        """Шов §8.10: build() принимает History, а не прогон адаптера."""
        import inspect
        from agent.profile import build as fn
        params = list(inspect.signature(fn).parameters)
        self.assertEqual(params, ["history", "settings", "segments"])


if __name__ == "__main__":
    unittest.main()
