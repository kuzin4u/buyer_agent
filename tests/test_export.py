"""Экспорт профиля наружу (SPEC §9). Схема заморожена в С1.

Главное, что проверяется, — не форма JSON, а два запрета: наружу не уходит
история, и наружу не уходит то, чему нельзя верить.
"""

import json
import os
import re
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fixture  # noqa: E402

from agent import export as E                                    # noqa: E402
from agent.matching import Horizon, Mode                          # noqa: E402


class ExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.history = fixture.history()
        cls.profile = fixture.profile()
        cls.payload = E.build(cls.profile, cls.history)

    def test_matches_the_frozen_schema(self):
        self.assertEqual(E.validate(self.payload), [])
        self.assertEqual(set(self.payload), set(E.SCHEMA))
        self.assertEqual(self.payload["schema_version"], E.SCHEMA_VERSION)

    def test_build_refuses_to_emit_something_off_schema(self):
        """Схема заморожена: несоответствие — дефект экспортёра, а не «почти»."""
        problems = E.validate({"schema_version": "0.9"})
        self.assertTrue(problems)

    def test_history_does_not_leak(self):
        """Наружу уходит профиль, а не история (SPEC §9)."""
        blob = json.dumps(self.payload, ensure_ascii=False)
        for key in E.EXCLUDED:
            self.assertNotIn(f'"{key}"', blob)

    def test_the_only_date_in_the_export_is_when_it_was_built(self):
        """Дат отдельных покупок в экспорте нет: получателю нужен профиль."""
        found = set(re.findall(r"\d{4}-\d{2}-\d{2}",
                               json.dumps(self.payload, ensure_ascii=False)))
        self.assertEqual(found, {self.payload["generated_at"][:10]})

    def test_basket_carries_frequency_not_receipts(self):
        for line in self.payload["basket"]:
            self.assertEqual(set(line), {"group", "dept", "per_month", "typical"})
            self.assertGreater(line["per_month"], 0)

    def test_anchors_are_inside_one_product_only(self):
        """Ориентир по смешанному ключу — не ориентир (Р-21)."""
        for anchor in self.payload["price_anchors"]:
            self.assertGreaterEqual(anchor["observations"], E.MIN_OBSERVATIONS)
            self.assertNotIn("— · —", anchor["label"])
            self.assertIn(anchor["unit"], ("kg", "l", "pcs"))

    def test_horizon_is_declared(self):
        """Получатель должен знать, чему верить (SPEC §8.10)."""
        self.assertEqual(self.payload["horizon"], Mode.REPEATABLE.value)
        other = E.build(self.profile, self.history,
                        horizon=Horizon.of(Mode.ONE_OFF.value))
        self.assertEqual(other["horizon"], Mode.ONE_OFF.value)

    def test_frequencies_match_the_basket(self):
        self.assertEqual(set(self.payload["frequencies"]),
                         {line["group"] for line in self.payload["basket"]})

    def test_export_is_json_serialisable(self):
        blob = json.dumps(self.payload, ensure_ascii=False)
        self.assertEqual(json.loads(blob)["schema_version"], E.SCHEMA_VERSION)

    def test_stable_between_runs(self):
        """Один и тот же профиль даёт один и тот же экспорт — кроме отметки времени."""
        again = E.build(self.profile, self.history,
                        asof=self.payload["generated_at"])
        self.assertEqual(again, self.payload)


if __name__ == "__main__":
    unittest.main()
