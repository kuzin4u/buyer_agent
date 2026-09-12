"""Слой ввода: очистка формата и складывание Ё (docs/DECISIONS.md Р-7, П-3).

Отсечение служебного префикса — разбор входа, а не знание о товаре, поэтому
правило проверяется здесь, рядом со слоем ввода, а не среди словарей.
"""

import copy
import os
import sys
import unittest
from dataclasses import replace

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fixture  # noqa: E402

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


class BrandOrderTest(unittest.TestCase):
    """Бренд спрашивается ДО извлечения фасовки.

    У обрезанных кассой названий фасовки в тексте нет, но бренд есть. Пока
    find_brand вызывался после успешного извлечения фасовки, 765 покупок по
    12 маркам выглядели пробелом в словаре, хотя правила в brands.json были.
    """

    @classmethod
    def setUpClass(cls):
        cls.result = fixture.run()
        cls.by_name = {}
        for item in cls.result.items:
            cls.by_name.setdefault(item.name, item)

    def test_brand_known_without_pack(self):
        item = self.by_name.get("ВОДА БОРЖОМИ МИН.ГИД")
        self.assertIsNotNone(item)
        self.assertIsNone(item.pack, "у этого названия фасовки в тексте нет")
        self.assertEqual(item.brand, "Боржоми")

    def test_truncated_names_still_resolve(self):
        for name, brand in (("АЛД.ВОДА ЕСС.ЦЕЛ.МИН", "Ессентуки"),
                            ("СВЕТ.МОЛОКО ЦЕЛ.3,3-", "Свитлогорье")):
            item = self.by_name.get(name)
            self.assertIsNotNone(item, name)
            self.assertEqual(item.brand, brand, name)

    def test_weighted_goods_have_no_brand(self):
        """SPEC §5: у весового бренд неприменим, а не неизвестен."""
        weighted = [i for i in self.result.items if i.weighted]
        self.assertTrue(weighted)
        self.assertTrue(all(i.brand is None for i in weighted))


class SelfDeterminedAttributesTest(unittest.TestCase):
    """Р-14: знаемое не должно жить в ветке незнаемого.

    Признак, который определяется самим названием, обязан вычисляться до
    всякого ветвления. Иначе он молча теряется на позициях, где предыдущий шаг
    не сработал, и выглядит это как пробел в словаре, а не как дефект кода.
    """

    @classmethod
    def setUpClass(cls):
        cls.result = fixture.run()
        cls.no_group = [i for i in cls.result.items
                        if i.group is None and not i.excluded]

    def test_weightedness_known_without_category(self):
        """«КАРТОФ ЕГИП ВЕС» весовой, даже если категория не распозналась."""
        weighted = [i for i in self.no_group if i.weighted]
        self.assertGreater(len(weighted), 300)
        for item in weighted:
            self.assertIsNotNone(item.weighted_reason)

    def test_brand_known_without_category(self):
        from agent.adapters.receipts_fns.sku import UNKNOWN
        branded = [i for i in self.no_group if i.brand and i.brand != UNKNOWN]
        self.assertGreater(len(branded), 100)

    def test_fat_known_without_category(self):
        self.assertGreater(len([i for i in self.no_group if i.fat]), 100)

    def test_fat_known_for_weighted_goods(self):
        """§5 объявляет неприменимыми бренд и штучную фасовку. Жирности там нет."""
        weighted = [i for i in self.result.items if i.weighted]
        self.assertGreater(len([i for i in weighted if i.fat]), 150)

    def test_true_dependencies_are_kept(self):
        """Что зависит по существу — должно зависеть и в коде."""
        for item in self.result.items:
            if item.key is not None:
                self.assertIsNotNone(item.group, item.name)
            if item.pack is not None:
                self.assertIsNotNone(item.group, item.name)
            if item.unit_price is not None:
                self.assertTrue(item.weighted or item.pack, item.name)
            if item.dept is not None:
                self.assertIsNotNone(item.group, item.name)


class RunResidueTest(unittest.TestCase):
    """Ни одна позиция прогона не должна унести служебный мусор в SKU."""

    @classmethod
    def setUpClass(cls):
        cls.result = fixture.run()
        cls.cov = coverage(cls.result)

    def test_no_prefix_residue(self):
        self.assertEqual([i.name for i in self.result.items if RE_PREFIX.match(i.name)], [])

    def test_no_double_spaces(self):
        self.assertEqual([i.name for i in self.result.items if "  " in i.name], [])

    def test_weighted_keys_carry_no_residue(self):
        for key in self.cov.bulk_skus:
            self.assertNotRegex(key.parts[0], RE_PREFIX)
            self.assertNotIn("  ", key.parts[0])


class BulkKeyTest(unittest.TestCase):
    """Хвост единицы измерения у весового названия (DECISIONS.md Р-15, П-1)."""

    @classmethod
    def setUpClass(cls):
        cls.rules = Rules(Config.load(BASE))

    def key(self, name):
        return N.bulk_key(self.rules, name)

    def test_rules_come_from_config_not_code(self):
        names = [name for name, _rx, _repl in self.rules.bulk_key]
        self.assertIn("хвост единицы измерения", names)

    def test_six_spellings_of_bananas_give_one_key(self):
        spellings = ["БАНАНЫ", "БАНАНЫ 1КГ", "БАНАНЫ ВЕС", "БАНАНЫ ВЕС 1КГ",
                     "БАНАНЫ ВЕСОВЫЕ", "БАНАНЫ,КГ"]
        self.assertEqual({self.key(n) for n in spellings}, {"БАНАНЫ"})

    def test_trailing_forms(self):
        for raw, expect in [
            ("МОРКОВЬ ВЕСОВАЯ 1КГ", "МОРКОВЬ"),
            ("АПЕЛЬСИНЫ ИМПОРТ ВЕС. 1КГ", "АПЕЛЬСИНЫ ИМПОРТ"),
            ("КАБАЧКИ 1 КГ", "КАБАЧКИ"),
            ("АПЕЛЬСИНЫ В СЕТКЕ ЦЕНА ЗА 1КГ", "АПЕЛЬСИНЫ В СЕТКЕ"),
        ]:
            self.assertEqual(self.key(raw), expect, raw)

    def test_word_in_the_middle_is_not_a_tail(self):
        """«ВЕС» внутри названия — часть названия, правило привязано к концу."""
        for raw in ["ПЕЛЬМЕНИ МЯСНОВЪ ПО-ЦАРСКИ ВЕС. ЗАВОД МЯСНОВЪ",
                    "ПАСТ.МОЛОКО ВЕС.МОЛ",
                    "ДЕСЕРТ ДОБРЯНКА ФУНДУК ВЕС АККОНД"]:
            self.assertEqual(self.key(raw), raw)

    def test_real_pack_size_is_not_stripped(self):
        """Единица измерения — только «1 КГ». «5КГ» — настоящая фасовка."""
        self.assertEqual(self.key("КАРТОФЕЛЬ 5КГ"), "КАРТОФЕЛЬ 5КГ")

    def test_suffix_inside_a_word_is_not_a_tail(self):
        self.assertEqual(self.key("ПОДВЕС"), "ПОДВЕС")

    def test_trailing_dot_is_kept(self):
        """Сокращения — задача словаря, а не разбора единицы измерения."""
        self.assertEqual(self.key("ЛОПАТКА СВИНАЯ ОХЛ."), "ЛОПАТКА СВИНАЯ ОХЛ.")

    def test_name_is_never_emptied(self):
        self.assertEqual(self.key("ВЕС 1КГ"), "ВЕС 1КГ")


class PackGroupsFromConfigTest(unittest.TestCase):
    """Множества групп для правил фасовки живут в конфиге (DECISIONS.md Р-16, П-2)."""

    @classmethod
    def setUpClass(cls):
        cls.config = Config.load(BASE)
        cls.rules = Rules(cls.config)

    def test_sets_match_the_config(self):
        by_name = {r["name"]: r
                   for r in self.config.normalization["pack_extraction"]["rules"]}
        self.assertEqual(self.rules.drink_groups,
                         frozenset(by_name["голый объём напитка"]["only_if_groups"]))
        ml = by_name["голое число в конце названия"]["unit_by_group"]["мл"]
        self.assertEqual({g for g, u in self.rules.bare_unit.items() if u == "l"},
                         set(ml))

    def test_group_ids_exist_in_categories(self):
        known = {g["id"] for g in self.config.categories["groups"]}
        self.assertLessEqual(self.rules.drink_groups, known)
        self.assertLessEqual(set(self.rules.bare_unit), known)

    def test_unknown_group_id_is_refused_loudly(self):
        """Опечатка в id обязана падать, а не тихо выключать правило."""
        norm = copy.deepcopy(self.config.normalization)
        rule = next(r for r in norm["pack_extraction"]["rules"]
                    if r["name"] == "голый объём напитка")
        rule["only_if_groups"] = ["napitki_kotoryh_net"]
        broken = replace(self.config, normalization=norm)
        with self.assertRaises(ValueError) as e:
            Rules(broken)
        self.assertIn("napitki_kotoryh_net", str(e.exception))

    def test_bare_number_reads_as_ml_for_liquids_and_g_otherwise(self):
        self.assertEqual(N.extract_pack(self.rules, "МАЦУН ФМ 3,2 500", "moloko"),
                         ("l", 0.5, "голое число (мл)"))
        self.assertEqual(N.extract_pack(self.rules, "МАСЛО ВКУСН.82,5 400", "maslo_sl"),
                         ("kg", 0.4, "голое число (г)"))

    def test_bare_litres_only_for_drinks(self):
        self.assertEqual(N.extract_pack(self.rules, "ПИВО КЕРСАРИ СВ 0,45", "pivo"),
                         ("l", 0.45, "литраж без единицы"))
        self.assertIsNone(N.extract_pack(self.rules, "ЧАЙ ГРИНФИЛД 0,45", "chay_kofe"))


class BulkKeyOnDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = fixture.run()
        cls.cov = coverage(cls.result)

    def test_no_measurement_tail_left_in_keys(self):
        tail = re.compile(r"(?:^|[\s,.])(?:1\s*)?(?:КГ|ВЕСОВ[А-Я]*|ВЕС)\.?\s*$")
        left = [k.parts[0] for k in self.cov.bulk_skus if tail.search(k.parts[0])]
        self.assertEqual(left, [])

    def test_displayed_name_keeps_the_tail(self):
        """Ключ сопоставительный, название — как в чеке (SPEC §11)."""
        shown = [i.name for i in self.result.items if i.name.endswith(" ВЕС")]
        self.assertTrue(shown, "в выборке есть названия, оканчивающиеся на ВЕС")


if __name__ == "__main__":
    unittest.main()
