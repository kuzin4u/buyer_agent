"""Слой ввода: очистка формата и складывание Ё (docs/DECISIONS.md Р-7, П-3).

Отсечение служебного префикса — разбор входа, а не знание о товаре, поэтому
правило проверяется здесь, рядом со слоем ввода, а не среди словарей.
"""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from agent.config import Config, dataset_path                       # noqa: E402
from agent.adapters.receipts_fns import Pipeline, load_receipts     # noqa: E402
from agent.adapters.receipts_fns.rules import Rules                 # noqa: E402
from agent.adapters.receipts_fns import normalize as N              # noqa: E402
from agent.adapters.receipts_fns.diagnostics import coverage        # noqa: E402

import re

RE_PREFIX = re.compile(r"^\s*\d+:\s*\d+\s+")


class CleanupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = Rules(Config.load(BASE))

    def test_rules_come_from_config_not_code(self):
        """Правила очистки читаются из normalization.json (SPEC §11)."""
        names = [name for name, _rx, _repl in self.rules.cleanup]
        self.assertIn("префикс «номер строки: артикул»", names)
        self.assertIn("схлопывание пробелов", names)

    def test_line_number_and_article_stripped(self):
        self.assertEqual(
            N.clean(self.rules, "10: 4205427 АЛД.Вода ЕСС.ЦЕЛ.мин.газ.0.5"),
            "АЛД.ВОДА ЕСС.ЦЕЛ.МИН.ГАЗ.0.5")

    def test_same_item_different_line_numbers_collapses(self):
        """Номер строки не должен разводить один товар на разные ключи."""
        variants = [f"{n}: 4205427 АЛД.Вода ЕСС.ЦЕЛ.мин.газ.0.5" for n in (1, 10, 23)]
        self.assertEqual(len({N.clean(self.rules, v) for v in variants}), 1)

    def test_runs_of_spaces_collapse(self):
        self.assertEqual(N.clean(self.rules, "БАНАНЫ           1КГ"), "БАНАНЫ 1КГ")
        self.assertEqual(N.clean(self.rules, "  ЛИМОНЫ 1КГ  "), "ЛИМОНЫ 1КГ")

    def test_article_inside_name_is_not_touched(self):
        """Правило якорится в начало: число внутри названия — не префикс."""
        self.assertEqual(N.clean(self.rules, "СМЕТАНА 25%300Г"), "СМЕТАНА 25%300Г")
        self.assertEqual(N.clean(self.rules, "ЯЙЦО СМЕТ С0 20ШТ"), "ЯЙЦО СМЕТ С0 20ШТ")

    def test_homoglyphs_still_word_wise(self):
        """Очистка не должна испортить правило §5.1: PATERRA остаётся латинской."""
        self.assertEqual(N.clean(self.rules, "PATERRA"), "PATERRA")
        self.assertEqual(N.clean(self.rules, "ПАKЕТ"), "ПАКЕТ")


class FoldTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = Rules(Config.load(BASE))

    def test_display_keeps_yo(self):
        """Пользователь видит название так, как оно написано в чеке."""
        self.assertEqual(N.clean(self.rules, "СЁМГА 300Г"), "СЁМГА 300Г")

    def test_matching_form_folds_yo(self):
        self.assertEqual(N.fold(self.rules, "СЁМГА 300Г"), "СЕМГА 300Г")

    def test_yo_and_ye_meet_in_one_key(self):
        a = N.fold(self.rules, N.clean(self.rules, "СЁМГА 300Г"))
        b = N.fold(self.rules, N.clean(self.rules, "СЕМГА 300Г"))
        self.assertEqual(a, b)


class RunResidueTest(unittest.TestCase):
    """Ни одна позиция прогона не должна унести служебный мусор в SKU."""

    @classmethod
    def setUpClass(cls):
        run = Pipeline(Config.load(BASE)).run(load_receipts(dataset_path(BASE)))
        cls.result = run
        cls.cov = coverage(run)

    def test_no_prefix_residue(self):
        self.assertEqual([i.name for i in self.result.items if RE_PREFIX.match(i.name)], [])

    def test_no_double_spaces(self):
        self.assertEqual([i.name for i in self.result.items if "  " in i.name], [])

    def test_weighted_keys_carry_no_residue(self):
        for key in self.cov.bulk_skus:
            self.assertNotRegex(key.parts[0], RE_PREFIX)
            self.assertNotIn("  ", key.parts[0])


if __name__ == "__main__":
    unittest.main()
