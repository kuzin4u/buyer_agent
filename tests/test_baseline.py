"""Эталонный тест: прогон обязан воспроизводить docs/BASELINE.txt построчно.

Любая правка конфига или кода, сдвинувшая хоть одну цифру, роняет этот тест.
Дальше это либо объяснённое улучшение — тогда эталон обновляется тем же
коммитом, — либо регресс (docs/DECISIONS.md Р-6).

Запуск:  python3 -m unittest discover -s tests
"""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fixture  # noqa: E402

from agent.config import Config, dataset_path                      # noqa: E402
from agent.adapters.receipts_fns import Pipeline, load_receipts    # noqa: E402
from agent.adapters.receipts_fns.report import render              # noqa: E402


class BaselineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # НЕ называть это .run: затенит TestCase.run и unittest сломается
        cls.result = fixture.run()
        cls.report = render(cls.result)
        with open(os.path.join(BASE, "docs", "BASELINE.txt"), encoding="utf-8") as f:
            cls.baseline = f.read().rstrip("\n")

    def test_report_matches_baseline_line_by_line(self):
        actual = self.report.split("\n")
        expected = self.baseline.split("\n")
        self.assertEqual(len(actual), len(expected), "изменилось число строк отчёта")
        for i, (a, e) in enumerate(zip(actual, expected), start=1):
            self.assertEqual(a, e, f"строка {i} разошлась с эталоном")

    def test_control_figures(self):
        """Контрольные цифры ТЗ — отдельно от текста отчёта."""
        n, s = self.result.tier_stat["food_core"]
        self.assertEqual(n, 839)
        self.assertEqual(round(s), 3693733)
        self.assertEqual(self.result.receipts_count, 1744)
        self.assertEqual(self.result.unknown_food_count, 158)


if __name__ == "__main__":
    unittest.main()
