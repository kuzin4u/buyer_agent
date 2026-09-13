"""Базовый слой §8.9: кнопки и разбор запроса регулярками, без ЛЛМ.

Проверяется не «понял ли он фразу», а два свойства, без которых базовый слой
опасен: непонятое называется непонятым, а понятое объясняет, ЧЕМ оно понято.
Молчаливо подставленный параметр — ошибка, неотличимая от правды: «корзина на
2000» и «корзина на неделю» дают совершенно разные ответы.
"""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from agent.core import Parser, SCENARIOS, load_intents                # noqa: E402
from agent.core.parse import normalize                                # noqa: E402


class IntentsConfigTest(unittest.TestCase):
    """Словарь формулировок — конфиг, и его целостность проверяется прогоном."""

    @classmethod
    def setUpClass(cls):
        cls.intents = load_intents(BASE)

    def test_every_scenario_in_config_exists_in_the_registry(self):
        """Формулировка, ведущая в несуществующий сценарий, — ошибка конфига."""
        for spec in self.intents["scenarios"]:
            self.assertIn(spec["id"], SCENARIOS, spec["id"])

    #: Восемь функций ядра по SPEC §8.1–8.8. Список именной, а не длина: он
    #: проверяет СВОЙСТВО — что каждая функция достижима, — и потому не падает
    #: от появления сводного сценария поверх них (Р-17).
    CORE_EIGHT = frozenset({"profile", "basket", "budget", "lapsed", "prices",
                            "venues", "choose", "spending"})

    def test_every_core_function_is_reachable(self):
        """Все восемь функций ядра должны быть доступны из браузера (§8.11)."""
        declared = {s["id"] for s in self.intents["scenarios"]}
        self.assertEqual(declared, set(SCENARIOS))
        self.assertLessEqual(self.CORE_EIGHT, declared,
                             "функция ядра пропала из конфига формулировок")

    def test_every_scenario_has_a_button_and_a_hint(self):
        for spec in self.intents["scenarios"]:
            self.assertTrue(spec.get("title"), spec["id"])
            self.assertTrue(spec.get("hint"), spec["id"])
            self.assertTrue(spec.get("patterns"), spec["id"])

    def test_button_arguments_are_accepted_by_their_scenario(self):
        """Кнопка не должна передавать параметр, которого сценарий не знает."""
        for spec in self.intents["scenarios"]:
            scenario = SCENARIOS[spec["id"]]
            for name in spec.get("button", {}):
                self.assertIn(name, scenario.params, f"{spec['id']}.{name}")

    def test_button_arguments_satisfy_required_parameters(self):
        """Кнопка обязана работать с одного нажатия, без доспрашивания."""
        for spec in self.intents["scenarios"]:
            scenario = SCENARIOS[spec["id"]]
            self.assertEqual(scenario.missing(spec.get("button", {})), (),
                             spec["id"])


class ParseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parser = Parser(base=BASE)

    def parse(self, query):
        return self.parser.parse(query, SCENARIOS)

    def test_normalize_folds_yo(self):
        """Ё→Е — то же правило, что у названий товаров (П-3)."""
        self.assertEqual(normalize("  Недёля  "), "неделя")

    def test_typical_queries_reach_their_function(self):
        cases = {
            "уложись в 2000": "budget",
            "что давно не покупал": "lapsed",
            "что подорожало": "prices",
            "где дешевле": "venues",
            "в какой магазин ехать": "choose",
            "траты по отделам": "spending",
            "мой профиль": "profile",
            "корзина на неделю": "basket",
        }
        for query, expected in cases.items():
            self.assertEqual(self.parse(query).scenario, expected, query)

    def test_parameters_are_extracted(self):
        self.assertEqual(self.parse("уложись в 2 000 ₽").params["amount"], 2000)
        self.assertEqual(self.parse("корзина на месяц").params["period"], "month")
        self.assertEqual(self.parse("траты по магазинам").params["by"], "venue")
        self.assertTrue(self.parse("динамика цен по всем товарам").params["all"])

    def test_window_is_not_mistaken_for_money(self):
        """«За 6 месяцев» — ширина окна, а не 6 ₽."""
        intent = self.parse("где траты растут по группам за 6 месяцев")
        self.assertEqual(intent.scenario, "spending")
        self.assertEqual(intent.params["months"], 6)
        self.assertNotIn("amount", intent.params)

    def test_period_word_is_not_mistaken_for_money(self):
        intent = self.parse("корзина на неделю")
        self.assertNotIn("amount", intent.params)

    def test_longer_phrase_wins_the_tie(self):
        """«Умная корзина» — это 8.6, хотя слово «корзина» есть и у 8.2."""
        intent = self.parse("умная корзина на неделю")
        self.assertEqual(intent.scenario, "venues")
        self.assertEqual(intent.params["period"], "week")
        self.assertIn("basket", intent.ambiguous)

    def test_sum_makes_the_question_prescriptive(self):
        """«Корзина на 2000» — это «уложись в 2000» (Р-22), и это видно в ответе."""
        intent = self.parse("корзина на 2000")
        self.assertEqual(intent.scenario, "budget")
        self.assertEqual(intent.params["amount"], 2000)
        self.assertIn("basket", intent.ambiguous)

    def test_unknown_query_is_called_unknown(self):
        """Непонятое не угадывается: это требование §8.9, а не осторожность."""
        for query in ("сколько стоит луна", "привет", "...", ""):
            intent = self.parse(query)
            self.assertFalse(intent.understood, query)
            self.assertFalse(intent.ready, query)

    def test_missing_required_parameter_is_named(self):
        """«Уложись» без суммы — понятый сценарий, но невыполнимый."""
        intent = self.parse("уложись")
        self.assertEqual(intent.scenario, "budget")
        self.assertEqual(intent.missing, ("amount",))
        self.assertFalse(intent.ready)

    def test_intent_explains_what_it_matched(self):
        """Пользователь обязан видеть, как его поняли."""
        intent = self.parse("уложись в 2000 на месяц")
        self.assertTrue(intent.matched)
        self.assertTrue(any("уложи" in m for m in intent.matched))
        self.assertTrue(any("2000" in m for m in intent.matched))

    def test_unknown_parameters_are_dropped_not_passed_on(self):
        """Сценарий получает только свои параметры, чужие не протекают."""
        intent = self.parse("что подорожало по месяцам")
        self.assertEqual(intent.scenario, "prices")
        self.assertNotIn("by", intent.params)

    def test_buttons_cover_every_scenario(self):
        """Кнопка есть у каждого сценария реестра, включая восемь функций ядра."""
        buttons = self.parser.buttons()
        self.assertEqual({b["id"] for b in buttons}, set(SCENARIOS))
        self.assertLessEqual(IntentsConfigTest.CORE_EIGHT,
                             {b["id"] for b in buttons})


class ScenarioRegistryTest(unittest.TestCase):
    def test_unknown_scenario_raises(self):
        from agent.core import run_scenario
        with self.assertRaises(KeyError):
            run_scenario("nonsense", None)

    def test_required_parameter_is_enforced_by_the_scenario(self):
        with self.assertRaises(ValueError):
            SCENARIOS["budget"](None)

    def test_accepts_filters_foreign_parameters(self):
        accepted = SCENARIOS["basket"].accepts({"period": "week", "by": "dept",
                                                "amount": 100})
        self.assertEqual(accepted, {"period": "week"})


if __name__ == "__main__":
    unittest.main()
