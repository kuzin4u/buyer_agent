"""Поток трат и настройки пользователя (SPEC §8.8, §3.3).

Контроль трат обязан охватывать ВСЕ деньги, включая непродуктовое и чеки без
опознанного продавца, — иначе он перестаёт быть контролем трат. При этом
контрольные цифры продуктового ядра не должны от этого сдвинуться.
"""

import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fixture  # noqa: E402

from agent.config import Config, dataset_path                       # noqa: E402
from agent.settings import Settings                                 # noqa: E402
from agent.adapters.receipts_fns import Pipeline, load_receipts     # noqa: E402
from agent.adapters.receipts_fns.pipeline import (                  # noqa: E402
    UNKNOWN_FOOD, UNKNOWN_VENUE, to_history)
from agent.adapters.receipts_fns.diagnostics import coverage        # noqa: E402


class OutlayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.receipts = list(fixture.receipts())
        cls.result = fixture.run()
        cls.history = fixture.history()

    def test_every_item_becomes_an_outlay(self):
        """Ни один рубль не теряется по дороге."""
        self.assertEqual(len(self.result.outlays),
                         sum(len(r.items) for r in self.receipts))

    def test_outlays_reconcile_with_receipt_totals(self):
        """Расхождение только от округления в самих чеках."""
        receipts_sum = sum(r.total for r in self.receipts)
        outlays_sum = sum(o.amount for o in self.result.outlays)
        self.assertLess(abs(receipts_sum - outlays_sum), 100)

    def test_non_food_money_is_counted(self):
        """non_food исключён из ценового анализа, но не из трат (SPEC §8.8)."""
        segments = {o.segment for o in self.result.outlays}
        self.assertIn("non_food", segments)
        self.assertIn("unknown", segments)

    def test_resolved_unknown_receipts_are_labelled(self):
        """Прошедшие разбор по составу получают магазин «Не определён» (§7)."""
        resolved = [i for i in self.result.items if i.segment == UNKNOWN_FOOD]
        self.assertTrue(resolved)
        self.assertEqual({i.venue for i in resolved}, {UNKNOWN_VENUE})

    def test_coverage_still_measures_food_core_only(self):
        """Разобранные безымянные не должны сдвинуть контрольные цифры ТЗ."""
        cov = coverage(self.result)
        self.assertEqual(cov.total, 15546)
        self.assertEqual(cov.matched, 11109)
        self.assertEqual(cov.with_pack, 8604)

    def test_history_carries_both_streams(self):
        self.assertTrue(self.history.events)
        self.assertTrue(self.history.outlays)
        start, end = self.history.span()
        self.assertTrue(start.startswith("2017"))
        self.assertTrue(end.startswith("2026"))


class SettingsTest(unittest.TestCase):
    def test_defaults_when_file_absent(self):
        """Отсутствие настроек — не ошибка: агент работает на умолчаниях."""
        with tempfile.TemporaryDirectory() as tmp:
            s = Settings.load(tmp)
        self.assertEqual(s.required_groups, ())
        self.assertFalse(s.include_candidates)
        self.assertEqual(s.rating("Глобус"), (None, None))

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            Settings(required_groups=("moloko", "hleb"), budget=3000,
                     venue_ratings={"Глобус": {"quality": 5, "ambience": 4}}).save(tmp)
            s = Settings.load(tmp)
        self.assertEqual(s.required_groups, ("moloko", "hleb"))
        self.assertEqual(s.budget, 3000)
        self.assertEqual(s.rating("Глобус"), (5, 4))


if __name__ == "__main__":
    unittest.main()
