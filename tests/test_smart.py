"""Умный слой §8.9: модель разбирает и объясняет, числа считает ядро.

Модель здесь поддельная. Это не упрощение, а единственный способ проверить то,
что важно: настоящая модель отвечает по-разному, а проверять надо не её, а нашу
реакцию на её ответ — в том числе на плохой. Поэтому здесь есть клиент, который
выдумывает числа, клиент, который падает, и клиент, который молчит.

Настоящих вызовов сети в тестах нет и быть не должно: тест, которому нужен ключ,
не запускается, а значит ничего не ловит.
"""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fixture  # noqa: E402

from agent.core import SCENARIOS, Session, run_scenario                # noqa: E402
from agent.core import smart as S                                      # noqa: E402
from agent.core.parse import Intent                                    # noqa: E402
from agent.core.verify import check, numbers                           # noqa: E402
from agent.settings import Settings                                    # noqa: E402


class Block:
    def __init__(self, type, **kw):
        self.type = type
        for key, value in kw.items():
            setattr(self, key, value)


class Response:
    def __init__(self, blocks):
        self.content = blocks
        self.usage = Block("usage", input_tokens=10, output_tokens=20)


class FakeClient:
    """Модель, которая отвечает заранее заданным. Записывает, о чём её спросили."""

    def __init__(self, tool=None, params=None, text="", fail=None):
        self.tool, self.params, self.text, self.fail = tool, params or {}, text, fail
        self.calls = []

    def call(self, system, messages, tools=None, max_tokens=None):
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        if self.fail:
            raise self.fail
        if tools:
            if self.tool is None:
                return Response([Block("text", text="НЕТ")])
            return Response([Block("tool_use", name=self.tool, input=self.params)])
        return Response([Block("text", text=self.text)])


def session():
    return Session(history=fixture.history(), settings=Settings())


class ToolSchemaTest(unittest.TestCase):
    """Схемы инструментов строятся из того же реестра, что кнопки и URL."""

    def test_every_scenario_becomes_a_tool(self):
        schemas = S.tool_schemas()
        self.assertEqual({s["name"] for s in schemas}, set(SCENARIOS))

    def test_schema_is_strict_and_closed(self):
        """«Почти правильный» вызов хуже отсутствия вызова."""
        for schema in S.tool_schemas():
            self.assertTrue(schema["strict"])
            self.assertFalse(schema["input_schema"]["additionalProperties"])

    def test_required_parameters_are_declared(self):
        budget = next(s for s in S.tool_schemas() if s["name"] == "budget")
        self.assertEqual(budget["input_schema"]["required"], ["amount"])

    def test_only_known_parameters_are_offered(self):
        for schema in S.tool_schemas():
            scenario = SCENARIOS[schema["name"]]
            for name in schema["input_schema"]["properties"]:
                self.assertIn(name, scenario.params, schema["name"])


class FactsTest(unittest.TestCase):
    """Модель видит то же, что человек в заголовке ответа."""

    @classmethod
    def setUpClass(cls):
        cls.session = session()

    def facts(self, scenario, **params):
        payload = run_scenario(scenario, self.session, **params)
        return payload, S.facts(scenario, payload)

    def test_every_scenario_has_facts(self):
        for scenario in SCENARIOS:
            self.assertIn(scenario, S.FACTS, scenario)

    def test_facts_are_not_empty(self):
        for scenario, params in (("profile", {}), ("basket", {"period": "week"}),
                                 ("budget", {"amount": 2000}), ("lapsed", {}),
                                 ("prices", {}), ("venues", {"period": "week"}),
                                 ("choose", {}), ("spending", {})):
            _payload, given = self.facts(scenario, **params)
            self.assertTrue(given.strip(), scenario)

    def test_misleading_internals_are_not_shown(self):
        """`single_totals` считается по РАЗНЫМ наборам строк у каждой площадки.

        «Магнит 240 ₽» оттуда выглядит лучшей ценой, покрывая одну строку из
        четырёх. Число настоящее, проверку чисел оно бы прошло — а вывод из него
        был бы ложным, поэтому модель его не видит.
        """
        payload, given = self.facts("venues", period="week")
        self.assertTrue(payload["route"].single_totals)
        for venue, total in payload["route"].single_totals.items():
            if venue == payload["route"].best_single:
                continue
            self.assertNotIn(f"{total:.0f}", given, venue)


class VerifyTest(unittest.TestCase):
    """Любое число ответа обязано быть в данных ядра (SPEC §8.9)."""

    @classmethod
    def setUpClass(cls):
        cls.session = session()
        cls.payload = run_scenario("venues", cls.session, period="week")
        cls.given = S.facts("venues", cls.payload)

    def test_quoting_the_data_passes(self):
        route = self.payload["route"]
        text = (f"Дешевле всего в одном месте — {route.baseline_total:.0f} ₽. "
                f"Врозь выйдет {route.split_total:.0f} ₽, экономия "
                f"{route.saving_abs:.0f} ₽.")
        self.assertTrue(check(text, self.given).ok, self.given)

    def test_invented_number_is_caught(self):
        self.assertFalse(check("Вы сэкономите 512 ₽.", self.given).ok)

    def test_price_age_reaches_the_model(self):
        """Возраст цены — часть ответа, и модель обязана его видеть (П-8)."""
        self.assertIn("мес", self.given)

    def test_plausible_extrapolation_is_caught(self):
        """Худший случай: число выведено верной арифметикой из верных данных.

        Экономия за неделю, умноженная на 52, — «экономия в год». Ядро этого не
        считало, значит посчитала модель, а считать ей нельзя.
        """
        saving = self.payload["route"].saving_abs
        year = f"{saving * 52:.0f}"
        verdict = check(f"Экономия {saving:.0f} ₽ в неделю — это {year} ₽ в год.",
                        self.given)
        self.assertFalse(verdict.ok)
        self.assertIn(year, verdict.invented)

    def test_number_from_internals_is_caught(self):
        """Число из ответа ядра, но не из показанных фактов, тоже отбраковано."""
        self.assertFalse(check("В Магните вышло бы 240 ₽.", self.given).ok)

    def test_text_without_numbers_passes(self):
        self.assertTrue(check("Стоит заехать в два магазина.", self.given).ok)

    def test_small_counts_are_allowed(self):
        """Запрещать «три магазина» значит запрещать русский язык."""
        self.assertTrue(check("Разложил корзину на 3 магазина.", self.given).ok)

    def test_rounding_is_allowed_but_wrong_rounding_is_not(self):
        saving = self.payload["route"].saving_abs
        self.assertTrue(check(f"Экономия {saving:.0f} ₽.", self.given).ok)
        self.assertFalse(check(f"Экономия {saving + 6:.0f} ₽.", self.given).ok)

    def test_verdict_names_what_was_invented(self):
        verdict = check("Итого 999 ₽ и ещё 888 ₽.", self.given)
        self.assertEqual(verdict.invented, ("999", "888"))
        self.assertIn("999", verdict.reason)

    def test_numbers_walks_dataclasses_and_properties(self):
        found = numbers(self.payload)
        self.assertIn(round(self.payload["route"].saving_abs, 2),
                      {round(x, 2) for x in found})


class SmartFlowTest(unittest.TestCase):
    """Полный проход: маршрут, счёт ядром, объяснение, проверка."""

    @classmethod
    def setUpClass(cls):
        cls.session = session()

    def test_model_chooses_the_function_and_core_computes(self):
        client = FakeClient(tool="venues", params={"period": "week"},
                            text="Дешевле всего покупать в одном месте.")
        result = S.answer("где дешевле корзина", self.session, client=client)
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(result.intent.scenario, "venues")
        self.assertIn("одном месте", result.text)
        # Ядро посчитало само: в ответе лежит его объект, а не текст модели.
        self.assertIn("route", result.payload)

    def test_model_never_sees_raw_history(self):
        """Модель получает только вопрос и посчитанные факты."""
        client = FakeClient(tool="choose", text="Лента дешевле остальных.")
        S.answer("куда ехать", self.session, client=client)
        sent = "\n".join(str(call["messages"]) for call in client.calls)
        self.assertNotIn("receipts", sent)
        self.assertNotIn("Outlay", sent)
        self.assertNotIn("Event(", sent)

    def test_invented_number_degrades_to_the_basic_layer(self):
        """Главная проверка §8.9: выдуманное число отбраковывает ответ целиком."""
        client = FakeClient(tool="venues", params={"period": "week"},
                            text="Экономия 512 ₽ в неделю.")
        result = S.answer("где дешевле", self.session, client=client)
        self.assertFalse(result.ok)
        self.assertIn("512", result.reason)
        self.assertIsNotNone(result.payload)      # числа ядра остались доступны

    def test_model_silence_degrades(self):
        client = FakeClient(tool=None)
        result = S.answer("что-то непонятное", self.session, client=client)
        self.assertFalse(result.ok)
        self.assertIn("не выбрала функцию", result.reason)

    def test_empty_explanation_degrades(self):
        client = FakeClient(tool="choose", text="   ")
        result = S.answer("куда ехать", self.session, client=client)
        self.assertFalse(result.ok)

    def test_network_failure_degrades(self):
        client = FakeClient(fail=ConnectionError("сеть недоступна"))
        result = S.answer("где дешевле", self.session, client=client)
        self.assertFalse(result.ok)
        self.assertIn("ConnectionError", result.reason)

    def test_any_exception_degrades(self):
        """Умный слой может добавить объяснение, но не отнять ответ."""
        for error in (ValueError("плохой ответ"), RuntimeError("лимит провайдера"),
                      KeyError("нет поля"), TimeoutError("долго")):
            result = S.answer("где дешевле", self.session,
                              client=FakeClient(fail=error))
            self.assertFalse(result.ok)
            self.assertTrue(result.reason)

    def test_basic_layer_intent_is_used_when_model_fails_to_route(self):
        """Регулярки разобрали — значит ответ есть, даже если модель не поняла."""
        client = FakeClient(tool=None, text="")
        fallback = Intent(query="где дешевле", scenario="venues", params={})
        result = S.answer("где дешевле", self.session, client=client,
                          fallback=fallback)
        # Модель не выбрала функцию, но разбор базового слоя принят.
        self.assertEqual(result.intent.scenario, "venues")

    def test_missing_required_parameter_degrades(self):
        client = FakeClient(tool="budget", params={})
        result = S.answer("уложись", self.session, client=client)
        self.assertFalse(result.ok)
        self.assertIn("amount", result.reason)

    def test_unknown_tool_name_degrades(self):
        client = FakeClient(tool="выдуманная_функция")
        result = S.answer("что-нибудь", self.session, client=client)
        self.assertFalse(result.ok)

    def test_foreign_parameters_are_dropped(self):
        client = FakeClient(tool="venues", params={"period": "week", "by": "dept"},
                            text="Корзина разводится на 4 магазина.")
        result = S.answer("где дешевле", self.session, client=client)
        self.assertNotIn("by", result.intent.params)


class LimitsTest(unittest.TestCase):
    """Лимиты: упереться в умный слой должно быть дёшево."""

    def test_long_query_is_refused_before_the_model(self):
        client = FakeClient(tool="venues")
        result = S.answer("а" * (S.MAX_QUERY_CHARS + 1), session(), client=client)
        self.assertFalse(result.ok)
        self.assertEqual(client.calls, [])      # до модели дело не дошло

    def test_empty_query_is_refused(self):
        self.assertFalse(S.answer("", session(), client=FakeClient()).ok)

    def test_rate_limit_stops_further_calls(self):
        limiter = S.Limiter(limit=1)
        client = FakeClient(tool="choose", text="Лента дешевле.")
        first = S.answer("куда ехать", session(), client=client, limiter=limiter)
        second = S.answer("куда ехать", session(), client=client, limiter=limiter)
        self.assertTrue(first.ok, first.reason)
        self.assertFalse(second.ok)
        self.assertIn("слишком часто", second.reason)

    def test_limiter_window_slides(self):
        limiter = S.Limiter(limit=1, window=10)
        self.assertTrue(limiter.allow(now=0))
        self.assertFalse(limiter.allow(now=5))
        self.assertTrue(limiter.allow(now=11))

    def test_without_a_key_the_layer_is_simply_off(self):
        """Без ключа — не ошибка, а штатный режим (SPEC §8.9)."""
        key = os.environ.pop(S.KEY_ENV, None)
        try:
            result = S.answer("где дешевле", session())
            self.assertFalse(result.ok)
            self.assertIn("базовый слой", result.reason)
        finally:
            if key is not None:
                os.environ[S.KEY_ENV] = key

    def test_key_is_read_from_the_server_environment_only(self):
        """Ключ живёт на сервере и в клиент не уходит (SPEC §8.9, §10)."""
        for path in ("web/app.py", "web/static/charts.js", "bot/main.py",
                     "bot/api.py", "web/templates/base.html"):
            with open(os.path.join(BASE, path), encoding="utf-8") as f:
                source = f.read()
            self.assertNotIn("ANTHROPIC_API_KEY", source, path)
            self.assertNotIn("sk-ant", source, path)


try:
    import anthropic
    SDK = True
except ImportError:
    SDK = False


@unittest.skipUnless(SDK, "SDK модели не установлен: умный слой необязателен")
class SdkContractTest(unittest.TestCase):
    """Запрос собирается из тех параметров, которые SDK действительно знает.

    Сети здесь нет и ключа не нужно: проверяется форма вызова, а не ответ. Без
    этой проверки опечатка в имени параметра всплыла бы только у пользователя с
    ключом — то есть в единственном месте, где её никто не ждёт.
    """

    def test_request_parameters_exist_in_the_sdk(self):
        import inspect
        signature = inspect.signature(anthropic.Anthropic(
            api_key="test-key").messages.create)
        for name in ("model", "max_tokens", "system", "messages", "tools",
                     "tool_choice", "output_config"):
            self.assertIn(name, signature.parameters, name)

    def test_model_is_the_current_default(self):
        self.assertEqual(S.MODEL, os.environ.get("BUYER_AGENT_MODEL",
                                                 "claude-opus-5"))

    def test_client_is_not_built_without_a_key(self):
        """Библиотека импортируется лениво: ядру она не нужна (Р-6)."""
        client = S.Client()
        self.assertIsNone(client._client)


if __name__ == "__main__":
    unittest.main()
