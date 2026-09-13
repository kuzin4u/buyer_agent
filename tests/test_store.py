"""Настройки §3.3 в SQLite (DECISIONS Р-4).

Хранилище проверяется отдельно от оболочки: настройки — состояние ядра, а не
веба. Через год рядом с формой появится бот (Р-1), и правила должны быть одни.
"""

import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from agent.settings import Settings                                  # noqa: E402
from agent.config import Config                                      # noqa: E402
from agent.store import RulesStore, SettingsStore, load_settings     # noqa: E402


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "state.db")
        self.store = SettingsStore(db_path=self.path, base=self.dir)

    def tearDown(self):
        self.store.close()

    def test_empty_database_gives_defaults(self):
        """Отсутствие настроек — не ошибка: агент работает на умолчаниях."""
        self.assertEqual(self.store.load(), Settings())

    def test_roundtrip_keeps_types(self):
        """Кортежи остаются кортежами, вложенный словарь — словарём."""
        self.store.update(required_groups=["syr", "moloko"], budget=2500,
                          include_candidates=True)
        loaded = self.store.load()
        self.assertEqual(loaded.required_groups, ("syr", "moloko"))
        self.assertEqual(loaded.budget, 2500)
        self.assertTrue(loaded.include_candidates)

    def test_update_does_not_erase_neighbours(self):
        """Веб меняет настройки по одной; соседняя не должна теряться."""
        self.store.update(required_groups=["syr"])
        self.store.update(budget=1000)
        loaded = self.store.load()
        self.assertEqual(loaded.required_groups, ("syr",))
        self.assertEqual(loaded.budget, 1000)

    def test_survives_reopening(self):
        self.store.update(budget=777)
        self.store.close()
        with SettingsStore(db_path=self.path, base=self.dir) as again:
            self.assertEqual(again.load().budget, 777)

    def test_ratings_are_validated_in_the_store(self):
        """Звёзды 1–5 — свойство данных (§8.7), а не поля ввода."""
        self.store.rate("Глобус", quality=5, ambience=4)
        self.assertEqual(self.store.load().venue_ratings["Глобус"],
                         {"quality": 5, "ambience": 4})
        for bad in (0, 6, -1):
            with self.assertRaises(ValueError):
                self.store.rate("Лента", quality=bad)

    def test_rating_can_be_removed(self):
        self.store.rate("Лента", quality=3)
        self.store.rate("Лента", quality="")
        self.assertNotIn("Лента", self.store.load().venue_ratings)

    def test_partial_rating_keeps_the_other_half(self):
        self.store.rate("Магнит", quality=4)
        self.store.rate("Магнит", ambience=2)
        self.assertEqual(self.store.load().venue_ratings["Магнит"],
                         {"quality": 4, "ambience": 2})

    def test_unknown_setting_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.update(nonsense=1)

    def test_unknown_row_from_the_future_does_not_break_loading(self):
        """Настройка из будущей версии не должна ломать старый код."""
        with self.store.conn:
            self.store.conn.execute(
                "INSERT INTO settings (key, value) VALUES ('alerts_enabled', 'true')")
        self.assertEqual(self.store.load(), Settings())

    def test_json_is_imported_once(self):
        """Перенос старого файла настроек — однократный и с отметкой.

        Иначе удалённая пользователем обязательная группа возвращалась бы из
        файла, который он уже не считает источником правды.
        """
        with open(os.path.join(self.dir, "settings.local.json"), "w",
                  encoding="utf-8") as f:
            f.write('{"required_groups": ["hleb"], "budget": 1500}')
        imported = self.store.import_json_once()
        self.assertEqual(imported.required_groups, ("hleb",))
        self.assertEqual(self.store.load().budget, 1500)

        self.store.update(required_groups=())
        self.assertIsNone(self.store.import_json_once())
        self.assertEqual(self.store.load().required_groups, ())

    def test_missing_json_is_not_an_error(self):
        self.assertIsNone(self.store.import_json_once())
        self.assertTrue(self.store.migrated())

    def test_load_settings_helper(self):
        self.store.update(budget=42)
        self.store.close()
        self.assertEqual(load_settings(base=self.dir, db_path=self.path).budget, 42)


class RulesStoreTest(unittest.TestCase):
    """Пополнения словарей пользователем (SPEC §8.11, Р-4)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = RulesStore(db_path=os.path.join(self.dir, "state.db"),
                                base=self.dir)

    def tearDown(self):
        self.store.close()

    def test_rules_start_empty_and_overlay_is_empty(self):
        self.assertEqual(self.store.rules(), [])
        self.assertEqual(self.store.overlay(), ([], []))

    def test_brand_and_category_rules_round_trip(self):
        self.store.add("brand", {"brand": "ДСК", "match": "ДСК ОГУРЦЫ"}, note="из очереди")
        self.store.add("category", {"group": "vypechka", "match": "ШОК.*МОЛ"})
        brands, categories = self.store.overlay()
        self.assertEqual(brands, [{"brand": "ДСК", "match": "ДСК ОГУРЦЫ",
                                   "note": "из очереди"}])
        self.assertEqual(categories, [{"group": "vypechka", "match": "ШОК.*МОЛ",
                                       "note": None}])

    def test_duplicate_rule_returns_nothing_not_a_foreign_id(self):
        """Иначе вызывающий решит, что создал правило, и запишет чужой эффект."""
        first = self.store.add("brand", {"brand": "A", "match": "A"})
        self.assertIsNotNone(first)
        self.assertIsNone(self.store.add("brand", {"brand": "A", "match": "A"}))
        self.assertEqual(len(self.store.rules()), 1)

    def test_rule_can_be_rolled_back(self):
        rule_id = self.store.add("brand", {"brand": "A", "match": "A"})
        self.store.remove(rule_id)
        self.assertEqual(self.store.rules(), [])

    def test_effect_is_remembered(self):
        """Без записанного эффекта «полезно ли правило» забывается через неделю."""
        rule_id = self.store.add("brand", {"brand": "A", "match": "A"})
        self.store.record_effect(rule_id, {"useful": True, "steps": []})
        self.assertTrue(self.store.rules()[0]["effect"]["useful"])

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.add("nonsense", {})

    def test_settings_and_rules_share_one_database(self):
        self.store.update(budget=100)
        self.store.add("brand", {"brand": "A", "match": "A"})
        self.assertEqual(self.store.load().budget, 100)
        self.assertEqual(len(self.store.rules()), 1)


class ConfigOverlayTest(unittest.TestCase):
    """Пользовательские правила ложатся поверх конфигов (agent/config.py)."""

    @classmethod
    def setUpClass(cls):
        cls.config = Config.load(BASE)

    def test_empty_overlay_changes_nothing(self):
        self.assertIs(self.config.with_rules(), self.config)

    def test_user_rules_are_checked_first(self):
        """Пользователь смотрит на позицию и говорит, чем она является: это
        утверждение сильнее общего правила из файла, значит проверяется раньше."""
        merged = self.config.with_rules(
            brands=[{"brand": "Тест", "match": "ТЕСТ"}],
            categories=[{"group": "vypechka", "match": "ШОК"}])
        self.assertEqual(merged.brands["brands"][0]["brand"], "Тест")
        self.assertEqual(merged.categories["groups"][0]["id"], "vypechka")
        self.assertTrue(merged.categories["groups"][0]["user"])

    def test_category_rule_inherits_dept_and_unit_of_its_group(self):
        """Пользователь сказал «это сладкое», а не «это новая группа»."""
        base = next(g for g in self.config.categories["groups"]
                    if g["id"] == "vypechka")
        merged = self.config.with_rules(
            categories=[{"group": "vypechka", "match": "ШОК"}])
        entry = merged.categories["groups"][0]
        self.assertEqual(entry["dept"], base["dept"])
        self.assertEqual(entry["unit"], base["unit"])

    def test_fingerprint_changes_with_the_overlay(self):
        """Отпечаток — ключ кэша: не поменяйся он, ответы остались бы старыми."""
        merged = self.config.with_rules(brands=[{"brand": "Т", "match": "Т"}])
        self.assertNotEqual(merged.fingerprint, self.config.fingerprint)
        again = self.config.with_rules(brands=[{"brand": "Т", "match": "Т"}])
        self.assertEqual(merged.fingerprint, again.fingerprint)

    def test_base_config_is_not_mutated(self):
        """Основа курируется и правится вручную с прогоном — её не трогаем."""
        groups = len(self.config.categories["groups"])
        brands = len(self.config.brands["brands"])
        self.config.with_rules(brands=[{"brand": "Т", "match": "Т"}],
                               categories=[{"group": "syr", "match": "X"}])
        self.assertEqual(len(self.config.categories["groups"]), groups)
        self.assertEqual(len(self.config.brands["brands"]), brands)

    def test_broken_regexp_is_refused_at_adding_time(self):
        """Неверная регулярка должна отвергаться сразу, а не ломать прогон потом."""
        for bad in ("[", "(?P<", "*"):
            with self.assertRaises(ValueError):
                self.config.with_rules(brands=[{"brand": "Т", "match": bad}])

    def test_rule_for_unknown_group_is_refused(self):
        with self.assertRaises(ValueError):
            self.config.with_rules(categories=[{"group": "нетакой", "match": "X"}])


if __name__ == "__main__":
    unittest.main()
