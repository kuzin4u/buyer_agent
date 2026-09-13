"""Веб-оболочка: все восемь функций доходят до браузера (SPEC §8.11).

Оболочка ничего не считает, поэтому здесь не проверяется арифметика — она
проверена в тестах ядра. Проверяется другое: каждая функция доступна, оговорки
ядра доходят до страницы не потеряв смысла, настройки §3.3 ложатся в базу и
меняют ответ, а свободный запрос попадает в ту же функцию, что кнопка.

Тесты пропускаются, если оболочка не установлена: ядро от неё не зависит и
должно проверяться на одной стандартной библиотеке (DECISIONS Р-6).
"""

import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

try:
    from fastapi.testclient import TestClient
    WEB = True
except ImportError:                                   # оболочка не установлена
    WEB = False

REASON = ("веб-оболочка не установлена: pip install -r requirements.txt "
          "(ядро от неё не зависит, Р-6)")


@unittest.skipUnless(WEB, REASON)
class WebTest(unittest.TestCase):
    """Один процесс на класс: прогон конвейера стоит около секунды."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        os.environ["BUYER_AGENT_DB"] = os.path.join(cls.dir, "state.db")
        import web.app as app_module
        cls.module = app_module
        cls.module.reset_state()
        cls.client = TestClient(app_module.app)

    @classmethod
    def tearDownClass(cls):
        cls.module.reset_state()
        os.environ.pop("BUYER_AGENT_DB", None)

    def setUp(self):
        """Настройки в начале каждого теста — умолчания, иначе порядок тестов
        начинает влиять на результат."""
        self.client.post("/settings", data={"budget": ""})

    def get(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return response.text

    # --- восемь функций ---

    def test_every_core_function_has_a_page(self):
        from agent.core import SCENARIOS
        urls = {"profile": "/profile", "basket": "/basket?period=week",
                "budget": "/budget?amount=2000", "lapsed": "/lapsed",
                "prices": "/prices", "venues": "/venues", "choose": "/choose",
                "spending": "/spending?by=year"}
        self.assertEqual(set(urls), set(SCENARIOS))
        for name, url in urls.items():
            self.assertIn("8.", self.get(url), name)

    def test_index_lists_all_eight_buttons(self):
        body = self.get("/")
        for name in ("Профиль предпочтений", "Корзина «как обычно»",
                     "Уложиться в сумму", "Что давно не покупал",
                     "Динамика цен", "Где что дешевле", "Выбор магазина",
                     "Контроль трат"):
            self.assertIn(name, body, name)

    def test_profile_shows_the_main_venue(self):
        self.assertIn("Глобус", self.get("/profile"))

    def test_basket_with_a_sum_points_at_8_3(self):
        """Два «бюджета» обязаны объяснять разницу (Р-22)."""
        body = self.get("/basket?budget=2000")
        self.assertIn("Это <b>8.2</b>", body)
        self.assertIn("/budget?amount=2000", body)

    def test_budget_page_shows_what_was_swapped(self):
        body = self.get("/budget?amount=15500&period=month")
        self.assertIn("Чем заменили", body)
        self.assertIn("Арза", body)
        self.assertIn("Покрытие рычага", body)
        self.assertIn("весовой товар", body)

    def test_budget_without_a_sum_asks_for_one(self):
        body = self.get("/budget")
        self.assertIn("Назовите сумму", body)

    def test_budget_reports_an_empty_basket_as_empty(self):
        body = self.get("/budget?amount=50")
        self.assertIn("не входит ничего", body)

    def test_prices_chart_carries_data_and_a_table(self):
        """График — пересказ таблицы, а не замена: на странице обязаны быть оба."""
        body = self.get("/prices")
        self.assertIn('id="prices-data"', body)
        self.assertIn("prices-chart", body)
        self.assertIn("<table>", body)

    def test_venues_smart_basket_shows_coverage(self):
        body = self.get("/venues?period=week")
        self.assertIn("корзины", body)
        self.assertIn("экономия", body.lower())

    def test_spending_growth_has_both_windows(self):
        body = self.get("/spending?by=group&growth=true&months=6")
        self.assertIn("против предыдущих", body)
        self.assertIn('id="growth-data"', body)

    def test_choose_explains_what_the_total_is_made_of(self):
        body = self.get("/choose")
        self.assertIn("Итог сложен из", body)

    # --- базовый слой §8.9 ---

    def test_free_text_reaches_the_same_function_as_the_button(self):
        cases = {"уложись в 2000": "/budget?amount=2000",
                 "корзина на неделю": "/basket?period=week",
                 "что давно не покупал": "/lapsed",
                 "траты по отделам": "/spending?by=dept",
                 "умная корзина на неделю": "/venues?period=week"}
        for query, expected in cases.items():
            response = self.client.get("/", params={"q": query},
                                       follow_redirects=False)
            self.assertEqual(response.status_code, 303, query)
            self.assertEqual(response.headers["location"], expected, query)

    def test_unknown_query_says_so_instead_of_guessing(self):
        body = self.client.get("/", params={"q": "сколько стоит луна"}).text
        self.assertIn("Не понял запрос", body)
        self.assertIn("без ЛЛМ", body)

    def test_query_without_a_sum_asks_for_the_sum(self):
        body = self.client.get("/", params={"q": "уложись"}).text
        self.assertIn("не хватает числа", body)

    def test_no_model_is_called(self):
        """§8.9: базовый слой работает офлайн и без ключей. Ни одного обращения
        к сети в оболочке быть не должно — ни за моделью, ни за графиком."""
        for path in ("web/app.py", "web/static/charts.js",
                     "web/templates/base.html"):
            with open(os.path.join(BASE, path), encoding="utf-8") as f:
                source = f.read()
            for forbidden in ("http://", "https://", "openai", "anthropic"):
                self.assertNotIn(forbidden, source, f"{path}: {forbidden}")

    # --- настройки §3.3 ---

    def test_settings_page_offers_everything_from_3_3(self):
        body = self.get("/settings")
        for name in ("required", "excluded", "budget", "main_venue",
                     "include_candidates", "quality", "ambience"):
            self.assertIn(f'name="{name}"', body, name)

    def test_settings_are_stored_and_change_the_answer(self):
        response = self.client.post("/settings", data={
            "required": ["konservy"], "excluded": ["pivo"], "budget": "2500"},
            follow_redirects=False)
        self.assertEqual(response.status_code, 303)

        from agent.store import SettingsStore
        with SettingsStore(db_path=os.environ["BUYER_AGENT_DB"]) as store:
            stored = store.load()
        self.assertEqual(stored.required_groups, ("konservy",))
        self.assertEqual(stored.budget, 2500)

        week = self.get("/basket?period=week")
        self.assertIn("konservy", week)          # обязательная вошла, хоть и редкая
        month = self.get("/basket?period=month")
        self.assertNotIn("pivo", month)          # исключённая не вошла, хоть и частая

    def test_contradictory_settings_are_refused(self):
        """Группа не может быть и обязательной, и исключённой."""
        response = self.client.post("/settings", data={
            "required": ["syr"], "excluded": ["syr"]})
        self.assertEqual(response.status_code, 200)
        self.assertIn("и обязательной, и исключённой", response.text)
        from agent.store import SettingsStore
        with SettingsStore(db_path=os.environ["BUYER_AGENT_DB"]) as store:
            self.assertEqual(store.load().required_groups, ())

    def test_stars_reach_the_choice_of_venue(self):
        for venue in ("Глобус", "Пятёрочка", "Магнит", "Перекрёсток", "Лента",
                      "Вкусвилл", "Дикси", "Ашан", "Метро", "Атак"):
            self.client.post("/settings/rating",
                             data={"venue": venue, "quality": "5", "ambience": "4"})
        body = self.get("/choose")
        self.assertIn("★★★★★", body)
        self.assertIn("качество", body)

    def test_candidate_tier_toggle_rebuilds_the_history(self):
        """Галочка меняет состав истории, а не только профиль: прогон заново.

        Это единственная настройка §3.3 с такой ценой, и если пересборка не
        произойдёт, ответы останутся прежними при изменённых настройках —
        ошибка, которую пользователь не увидит.
        """
        before = self.get("/profile")
        self.client.post("/settings", data={"include_candidates": "true"})
        after = self.get("/profile")
        self.assertNotEqual(before, after)
        self.assertTrue(self.module._state["session"].settings.include_candidates)
        self.client.post("/settings", data={"budget": ""})     # вернуть как было

    def test_pipeline_run_is_kept_for_the_diagnostics_panel(self):
        """Панель диагностики — С6, но прогон адаптера обязан дожить до неё.

        Выбросить его сейчас значит переделывать сессию потом: доля
        нераспознанных позиций, чем определена фасовка у каждой позиции и топы
        очередей на пополнение считаются именно по нему (SPEC §8.11).
        """
        run = self.module._state["session"].run
        self.assertIsNotNone(run)
        for attribute in ("items", "tier_stat", "unknown_food_count", "rules"):
            self.assertTrue(hasattr(run, attribute), attribute)

    def test_rating_out_of_range_is_refused_with_a_page_not_a_crash(self):
        """Форма такого не пришлёт, но подделанный запрос — это ответ
        пользователю, а не ошибка сервера."""
        response = self.client.post("/settings/rating",
                                    data={"venue": "Ларёк", "quality": "9"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("звёзды бывают от 1 до 5", response.text)

        from agent.store import SettingsStore
        with SettingsStore(db_path=os.environ["BUYER_AGENT_DB"]) as store:
            self.assertNotIn("Ларёк", store.load().venue_ratings)


if __name__ == "__main__":
    unittest.main()
