"""Веб-оболочка: все восемь функций доходят до браузера (SPEC §8.11).

Оболочка ничего не считает, поэтому здесь не проверяется арифметика — она
проверена в тестах ядра. Проверяется другое: каждая функция доступна, оговорки
ядра доходят до страницы не потеряв смысла, настройки §3.3 ложатся в базу и
меняют ответ, а свободный запрос попадает в ту же функцию, что кнопка.

Тесты пропускаются, если оболочка не установлена: ядро от неё не зависит и
должно проверяться на одной стандартной библиотеке (DECISIONS Р-6).
"""

import json
import os
import re
import shutil
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fixture  # noqa: E402

from agent import intake as I  # noqa: E402

try:
    from fastapi.testclient import TestClient
    WEB = True
except ImportError:                                   # оболочка не установлена
    WEB = False

REASON = ("веб-оболочка не установлена: pip install -r requirements.txt "
          "(ядро от неё не зависит, Р-6)")

def flat(markup):
    """Текст страницы со сжатыми пробелами.

    Шаблон переносит фразы по строкам, и проверка по подстроке ломается на любом
    переносе — на смысл при этом ничего не влияет. Сжимаем пробелы, чтобы тест
    проверял, что написано, а не как свёрстано.
    """
    return re.sub(r"\s+", " ", markup)



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
                "spending": "/spending?by=year",
                "plan": "/plan?period=week&amount=2000"}
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

    @fixture.slow
    def test_stars_reach_the_choice_of_venue(self):
        for venue in ("Глобус", "Пятёрочка", "Магнит", "Перекрёсток", "Лента",
                      "Вкусвилл", "Дикси", "Ашан", "Метро", "Атак"):
            self.client.post("/settings/rating",
                             data={"venue": venue, "quality": "5", "ambience": "4"})
        body = self.get("/choose")
        self.assertIn("★★★★★", body)
        self.assertIn("качество", body)

    @fixture.slow
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
        self.assertTrue(self.module.state()[0].settings.include_candidates)
        self.client.post("/settings", data={"budget": ""})     # вернуть как было

    def test_pipeline_run_is_kept_for_the_diagnostics_panel(self):
        """Панель диагностики — С6, но прогон адаптера обязан дожить до неё.

        Выбросить его сейчас значит переделывать сессию потом: доля
        нераспознанных позиций, чем определена фасовка у каждой позиции и топы
        очередей на пополнение считаются именно по нему (SPEC §8.11).
        """
        run = self.module.state()[0].run
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


@unittest.skipUnless(WEB, REASON)
class PlanPageTest(unittest.TestCase):
    """Сводный экран «продукты на неделю».

    Проверяется не вёрстка, а то, что оговорки ядра дошли до страницы. Экран
    сводит три функции в один ответ, и ровно поэтому на нём легче всего
    получить убедительную неправду: показать выбор магазина там, где выбора не
    было, или сложить два основания счёта в один итог.
    """

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

    def page(self, url="/plan?period=week&amount=2000"):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return flat(response.text)

    def money(self, value):
        """Сумма так, как она выглядит в сжатом тексте страницы.

        `money` разделяет разряды неразрывным пробелом, а `flat` сжимает любые
        пробелы в обычный. Без этого тест сравнивал бы «1 040» с «1\xa0040» и
        падал на форматировании, а не на смысле.
        """
        return flat(self.module.money(value))

    def plan(self, **kwargs):
        from agent.core import run_scenario
        session, _store, _parser = self.module.state()
        return run_scenario("plan", session, period="week", amount=2000,
                            **kwargs)["plan"]

    def test_coverage_is_stated_before_the_variants(self):
        """Ограничение стоит выше вариантов, а не в сноске под ними.

        Вариант, прочитанный без него, выглядит выбором магазина — тогда как
        у пяти строк из восьми магазин проставлен за неимением сравнения.
        """
        body = self.page()
        plan = self.plan()
        self.assertLess(body.index("Магазин известен не у всех позиций"),
                        body.index("Чем различаются варианты"),
                        "покрытие напечатано ниже вариантов")
        self.assertIn(f"у <b>{plan.priced}</b> из <b>{plan.considered}</b>", body)
        self.assertIn("не потому, что там дешевле", body)

    def test_every_line_without_a_shop_carries_its_reason(self):
        """Причина печатается у каждой такой строки, а не общей фразой."""
        body = self.page()
        plan = self.plan()
        self.assertTrue(plan.unpriced_reasons, "на датасете такие строки есть")
        for line, reason in plan.unpriced_reasons:
            self.assertIn(reason, body, line.group)

    def test_the_two_bases_are_named_on_the_page(self):
        """Цена окна и обычная трата названы разными деньгами, с числом разрыва."""
        body = self.page()
        plan = self.plan()
        self.assertIn("разные деньги", body)
        self.assertIn(self.money(plan.price_gap), body)
        self.assertIn(self.money(plan.usual_of_priced), body)

    def test_the_page_prints_no_grand_total(self):
        """Сумма двух оснований на странице не встречается ни в каком виде.

        Проверка не на формулировку, а на само число: если кто-нибудь сложит их
        в шаблоне, тест поймает результат, как бы он ни был подписан.
        """
        body = self.page()
        plan = self.plan()
        for variant in plan.variants:
            forbidden = self.money(variant.priced_total
                                   + variant.usual_total)
            self.assertNotIn(forbidden, body,
                             f"{variant.id}: на странице сложены два основания")

    def test_price_of_choice_names_what_it_costs_not_only_a_sum(self):
        """У каждого варианта назван не итог, а чем за него платят."""
        body = self.page()
        self.assertIn("Цена выбора", body)
        self.assertIn("лишних заезда", body)          # врозь: поездки
        self.assertIn("незакрытые потребности", body)  # под сумму: отказ
        self.assertIn("сверх развоза", body)           # в одном месте: переплата

    def test_choosing_a_variant_changes_the_list(self):
        """Выбор доходит до списка, а не только подсвечивает карточку."""
        single = self.page("/plan?period=week&amount=2000&choice=single")
        split = self.page("/plan?period=week&amount=2000&choice=split")
        self.assertIn("Список: всё в одном месте", single)
        self.assertIn("Список: врозь по магазинам", split)
        plan = self.plan(choice="split")
        for venue in plan.get("split").venues:
            self.assertIn(venue, split)

    def test_asking_for_the_budget_variant_without_a_sum_says_so(self):
        """Молча подставленный вариант — ошибка, неотличимая от правды (Р-23)."""
        body = self.page("/plan?period=week&choice=budget")
        self.assertIn("не построен: сумма не задана", body)

    def test_chosen_lines_are_distinguishable_from_default_ones(self):
        body = self.page("/plan?period=week&amount=2000&choice=split")
        self.assertIn("выбран по цене", body)
        self.assertIn("по умолчанию", body)

    def test_sending_the_list_queues_it_for_the_bot(self):
        """Веб наружу не ходит: он кладёт готовое в очередь, отправляет бот."""
        response = self.client.post(
            "/plan/send", data={"period": "week", "choice": "split",
                                "amount": "2000"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("поставлен в очередь", flat(response.text))

        taken = self.client.get("/api/outbox").json()["items"]
        self.assertEqual(len(taken), 1)
        self.assertEqual(taken[0]["kind"], "plan")
        payload = taken[0]["payload"]
        self.assertTrue(payload["venues"], "список ушёл без магазинов")
        self.assertIn("Врозь", payload["variant"])

        # Отдано один раз: отметка ставится до отправки, и повтор раздражает
        # сильнее пропуска.
        self.assertEqual(self.client.get("/api/outbox").json()["items"], [])

    def test_the_queued_list_carries_the_coverage_caveat(self):
        """Оговорка про магазины обязана дойти до мессенджера, а не остаться
        на странице: список без неё выглядит как совет, где выбора не было."""
        self.client.post("/plan/send", data={"period": "week",
                                             "choice": "split"})
        payload = self.client.get("/api/outbox").json()["items"][0]["payload"]
        notes = " ".join(payload["notes"])
        self.assertIn("не потому, что там дешевле", notes)
        plan = self.plan(choice="split")
        self.assertIn(f"{plan.priced} из {plan.considered}", notes)

        defaulted = [line for venue in payload["venues"]
                     for line in venue["lines"] if not line["chosen"]]
        self.assertTrue(defaulted, "в списке нет строк с магазином по умолчанию")
        for line in defaulted:
            self.assertTrue(line["reason"], "строка без причины")

    def test_the_queued_list_carries_no_raw_numbers_to_recompute(self):
        """Бот получает готовые строки: считать ему нечего и нечем (ОА-1)."""
        self.client.post("/plan/send", data={"period": "week",
                                             "choice": "single"})
        payload = self.client.get("/api/outbox").json()["items"][0]["payload"]
        for venue in payload["venues"]:
            for line in venue["lines"]:
                self.assertIsInstance(line["amount"], str)
                self.assertIn("₽", line["amount"])

    def test_free_query_reaches_the_plan(self):
        """«Продукты на неделю» словами ведёт на тот же экран, что кнопка."""
        response = self.client.get("/?q=продукты на неделю",
                                   follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn("/plan", response.headers["location"])


@fixture.slow
@unittest.skipUnless(WEB, REASON)
class IntakePageTest(unittest.TestCase):
    """Приём выгрузки через браузер: сохранить, склеить, померить, пересобрать.

    Медленный намеренно: приём обязан пересобрать историю конвейером, иначе
    профиль останется посчитанным по прежним чекам и соврёт (Р-4, Session).
    """

    @classmethod
    def setUpClass(cls):
        import web.app as app_module
        cls.module = app_module
        cls.client = TestClient(app_module.app)
        from agent.adapters.receipts_fns import load_dataset
        from agent.config import dataset_path
        cls.dataset = load_dataset(dataset_path(BASE))

    def setUp(self):
        """Свой приёмник и своя база на каждый тест.

        Приём меняет корпус, а корпус — это состояние: оставь его общим на
        класс, и тесты начнут зависеть от порядка. Здесь это стоит пересборки
        истории на каждый тест, потому класс и помечен медленным.
        """
        self.dir = tempfile.mkdtemp()
        os.environ["BUYER_AGENT_DB"] = os.path.join(self.dir, "state.db")
        os.environ["BUYER_AGENT_INBOX"] = os.path.join(self.dir, "inbox")
        self.module.reset_state()

    def tearDown(self):
        self.module.reset_state()
        os.environ.pop("BUYER_AGENT_DB", None)
        os.environ.pop("BUYER_AGENT_INBOX", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def upload(self, bodies, name="vygruzka.json"):
        blob = json.dumps(bodies, ensure_ascii=False).encode("utf-8")
        response = self.client.post(
            "/intake",
            files={"export": (name, blob, "application/json")})
        self.assertEqual(response.status_code, 200)
        return flat(response.text)

    def fresh(self, count, start=1):
        """Чеки, которых в эталоне нет: новые даты, суммы и реквизиты.

        Дата считается сложением дней, а не подстановкой числа в шаблон: «41
        января» разбор принимает молча, а конвейер падает на построении профиля.
        """
        import datetime
        from test_loader import as_fns
        first = datetime.date(2027, 1, 1) + datetime.timedelta(days=start)
        out = []
        for i in range(count):
            body = as_fns(self.dataset[0], fn="9287440300123456",
                          fd=7000 + start + i, fpd=8000 + start + i)
            day = first + datetime.timedelta(days=i)
            receipt = body["ticket"]["document"]["receipt"]
            receipt["dateTime"] = f"{day.isoformat()}T10:00:00"
            receipt["totalSum"] = 90000 + start + i
            out.append(body)
        return out

    def test_upload_is_stored_merged_and_measured(self):
        body = self.upload(self.fresh(3, start=1))
        self.assertIn("Принято:", body)
        self.assertIn("Нового в корпусе: <b>3</b>", body)

        # Файл лёг в приёмник как пришёл, а не как разобрался.
        stored = I.exports(base=BASE)
        self.assertEqual(len(stored), 1)
        self.assertTrue(stored[0].endswith(".json"))

        # Эффект записан и переживает перезагрузку страницы: пересчитать его
        # потом нельзя, он зависит от того, что уже лежало (Р-24).
        again = flat(self.client.get("/intake").text)
        self.assertIn("Что уже принято", again)
        _session, store, _parser = self.module.state()
        rows = store.accepted_exports()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["added"], 3)
        self.assertEqual(rows[0]["receipts"], 3)

    def test_the_same_file_twice_is_refused_with_a_reason(self):
        bodies = self.fresh(2, start=20)
        self.upload(bodies)
        body = self.upload(bodies)
        self.assertIn("Выгрузка не принята", body)
        self.assertIn("уже принята", body)

    def test_rubbish_is_refused_and_does_not_reach_the_inbox(self):
        before = len(I.exports(base=BASE))
        response = self.client.post(
            "/intake", files={"export": ("junk.json", b"not json",
                                         "application/json")})
        self.assertEqual(response.status_code, 200)
        self.assertIn("не разобрать", flat(response.text))
        self.assertEqual(len(I.exports(base=BASE)), before)

    def test_new_receipts_reach_the_answers(self):
        """Принятое обязано дойти до чисел, а не только до страницы приёма."""
        _session, _store, _parser = self.module.state()
        before = self.module.state()[0].history.span()[1]
        self.upload(self.fresh(2, start=40))
        after = self.module.state()[0].history.span()[1]
        self.assertGreater(after, before,
                           "история не пересобралась: профиль остался по старым "
                           "чекам")

    def test_the_page_separates_facts_from_the_guess(self):
        """Дубль по реквизитам и дубль по дате с суммой — разные утверждения."""
        from test_loader import as_fns
        bodies = []
        for i, receipt in enumerate(self.dataset[:4]):
            body = as_fns(receipt, fn="928744", fd=600 + i, fpd=600 + i)
            body["ticket"]["document"]["receipt"]["user"] = "СЫРОЕ ИМЯ ООО"
            bodies.append(body)
        body = self.upload(bodies, name="overlap.json")
        self.assertIn("это <b>догадка</b>", body)
        self.assertIn("по фискальным реквизитам — это факт", body)


@unittest.skipUnless(WEB, REASON)
class ApiTest(unittest.TestCase):
    """JSON API: то, чем пользуется бот (ОА-1) и экспорт профиля (§9)."""

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

    def json(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return response.json()

    def test_scenarios_are_listed_for_the_bot(self):
        """Боту виден весь реестр, включая восемь функций ядра.

        Проверяется состав, а не длина: список растёт сводными сценариями, и
        порог на длину мерил бы полноту конфига, а не свойство кода (Р-17).
        """
        from agent.core import SCENARIOS
        data = self.json("/api/scenarios")
        self.assertEqual({s["id"] for s in data["scenarios"]}, set(SCENARIOS))
        self.assertLessEqual({"profile", "basket", "budget", "lapsed", "prices",
                              "venues", "choose", "spending"},
                             {s["id"] for s in data["scenarios"]})
        self.assertIn("smart", data)

    def test_ask_answers_with_core_numbers(self):
        data = self.json("/api/ask?q=где дешевле")
        self.assertTrue(data["understood"])
        self.assertEqual(data["scenario"], "venues")
        self.assertIn("сравнимых товаров", data["facts"])

    def test_ask_by_scenario_name(self):
        data = self.json("/api/ask?scenario=choose")
        self.assertEqual(data["scenario"], "choose")
        self.assertTrue(data["facts"])

    def test_ask_reports_a_missing_parameter(self):
        data = self.json("/api/ask?q=уложись")
        self.assertEqual(data["missing"], ["amount"])

    def test_ask_says_when_it_did_not_understand(self):
        data = self.json("/api/ask?q=сколько стоит луна")
        self.assertFalse(data["understood"])
        self.assertTrue(data["buttons"])

    def test_without_a_key_the_answer_is_the_basic_layer(self):
        """§8.9: без ключа агент работает, а не отказывает."""
        key = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            data = self.json("/api/ask?q=где дешевле")
            self.assertFalse(data["smart"])
            self.assertTrue(data["facts"])
        finally:
            if key is not None:
                os.environ["ANTHROPIC_API_KEY"] = key

    def test_export_matches_the_frozen_schema(self):
        from agent import export
        data = self.json("/api/export")
        self.assertEqual(export.validate(data), [])

    def test_export_page_shows_what_is_excluded(self):
        body = flat(self.client.get("/export").text)
        self.assertIn("профиль, а не история", body)

    def test_notifications_do_not_repeat(self):
        """Память о напоминаниях живёт в ядре, а не в боте (ОА-1)."""
        first = self.json("/api/notifications")
        second = self.json("/api/notifications")
        self.assertTrue(first["items"])
        self.assertEqual(second["items"], [])


@unittest.skipUnless(WEB, REASON)
class DiagnosticsTest(unittest.TestCase):
    """Панель диагностики и пополнение словарей (SPEC §8.11).

    Главное здесь не таблицы, а мера: пополнение словаря — продолжение главной
    функции («варианты моей корзины в разных магазинах»), и после каждого
    правила видно, что стало со сравнимыми товарами и с разведённой корзиной.
    Свой класс, потому что каждое правило пересобирает историю заново.
    """

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

    def get(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return response.text

    def rules(self):
        _session, store, _parser = self.module.state()
        return store.rules()

    def reach(self):
        """Мера по текущему состоянию процесса.

        Состояние берётся через `state()`, а не из приватного словаря: словарь
        пуст, пока не собрана сессия, и тест, прочитавший его напрямую, работал
        только если до него уже сходил какой-то другой тест.
        """
        from agent import reach
        session, _store, _parser = self.module.state()
        return reach.measure(session.run, session.history, session.profile,
                             settings=session.settings)

    def add(self, **data):
        response = self.client.post("/diagnostics/rules", data=data,
                                    follow_redirects=False)
        return response

    def tearDown(self):
        """Правила не должны перетекать между тестами: каждое меняет историю."""
        for rule in self.rules():
            self.client.post(f"/diagnostics/rules/{rule['id']}/delete")

    # --- панель ---

    def test_panel_shows_the_whole_chain(self):
        body = self.get("/diagnostics")
        for name in ("распознана группа", "определена фасовка", "распознан бренд",
                     "сравнимых товаров", "строк корзины разводится"):
            self.assertIn(name, body.lower(), name)

    def test_panel_numbers_agree_with_the_pages(self):
        """Панель, противоречащая странице 8.6, хуже отсутствия панели."""
        reach = self.reach()
        body = flat(self.get("/diagnostics"))
        self.assertIn(f">{reach.comparable_shown}<", body)
        self.assertIn(f"{reach.split_lines} из {reach.basket_lines}", body)
        route = flat(self.get("/venues?period=week"))
        self.assertIn(f"{reach.split_lines} строкам из {reach.basket_lines}", route)

    def test_panel_prints_the_ceiling_next_to_the_answer(self):
        """Потолок по покрытию печатается рядом с достигнутым (Р-25)."""
        reach = self.reach()
        body = flat(self.get("/diagnostics"))
        self.assertIn(f"Потолок по покрытию — {reach.ceiling_lines} из "
                      f"{reach.basket_lines} строк", body)
        self.assertIn("потолок по покрытию", body.lower())

    def test_panel_turns_the_ceiling_into_a_work_queue(self):
        """П-7: потолок показывается заданиями, а не жалобой на ограничение."""
        body = flat(self.get("/diagnostics"))
        self.assertIn("Чего не хватает строкам корзины", body)
        self.assertIn("задание", body)
        self.assertIn("факт", body)
        self.assertIn("пополнением словаря не лечится", body)

    def test_queue_offers_a_rule_for_actionable_lines_only(self):
        """У задания есть форма разметки, у факта — нет."""
        from agent import reach
        session, _store, _parser = self.module.state()
        queue = reach.tasks(session.run, session.history, session.profile,
                            settings=session.settings)
        self.assertTrue([t for t in queue if t.actionable])
        self.assertTrue([t for t in queue if not t.actionable])
        body = self.get("/diagnostics")
        self.assertIn("Разметить", body)

    def test_smart_basket_shows_the_price_age(self):
        """Возраст цены печатается рядом с ценой (П-8)."""
        body = flat(self.get("/venues?period=week"))
        self.assertIn("возраст цены", body)
        self.assertIn("только современники", body)

    def test_smart_basket_names_the_skip_reason(self):
        """Непосчитанные строки названы причиной, а не свалены в остаток."""
        body = flat(self.get("/venues?period=week"))
        self.assertIn("сравнить не с чем (цена известна не у двух магазинов", body)

    def test_panel_does_not_promise_that_pools_need_one_rule(self):
        body = self.get("/diagnostics")
        self.assertIn("Смешанных пулов", body)
        self.assertIn("расщепляет", body)

    def test_effect_queue_is_shown_with_its_promise(self):
        """Очередь по эффекту на месте, и пустая она объясняет себя (П-8)."""
        body = flat(self.get("/diagnostics"))
        self.assertIn("Что разметить, чтобы прибавился сравнимый товар", body)
        self.assertIn("внутри окна сравнения", body)
        self.assertIn("Очередь пуста", body)

    def test_both_queues_and_pack_sources_are_on_the_panel(self):
        body = self.get("/diagnostics")
        self.assertIn("Очередь А", body)
        self.assertIn("Очередь Б", body)
        self.assertIn("Чем определена фасовка", body)

    # --- позиции: §8.11 буквально ---

    def test_positions_table_shows_why_each_pack_was_decided(self):
        body = self.get("/diagnostics/positions?search=огурцы")
        self.assertIn("чем определена", body)
        self.assertIn("весовой", body)

    def test_positions_can_be_filtered_to_brandless(self):
        body = self.get("/diagnostics/positions?brandless=true")
        self.assertIn("только без марки", body)

    # --- пополнение с измерением ---

    @fixture.slow
    def test_replenishment_measures_the_main_question(self):
        """То, ради чего всё: после правила видно движение ответа, а не процентов.

        Правило «ДСК ОГУРЦЫ» под окном сравнения ничего не двигает — эти покупки
        восьмилетней давности (П-8), — и панель обязана сказать именно это, а не
        нарисовать успех. Проверяется, что цепочка измерена и показана целиком.
        """
        response = self.add(kind="brand", brand="ДСК", match="ДСК ОГУРЦЫ")
        self.assertEqual(response.status_code, 303)
        body = self.get(response.headers["location"])
        self.assertIn("Что дало правило", body)
        self.assertIn("Сравнимо между магазинами", body)
        self.assertIn("Разводится по магазинам", body)
        self.assertIn("не сдвинулся", body)

    @fixture.slow
    def test_rule_effect_is_remembered_and_shown_later(self):
        """Эффект записан рядом с правилом — и когда он нулевой, тоже."""
        self.add(kind="brand", brand="ДСК", match="ДСК ОГУРЦЫ")
        rule = self.rules()[0]
        self.assertIn("useful", rule["effect"])
        decisive = [s for s in rule["effect"]["steps"] if s["decisive"]]
        self.assertTrue(decisive)
        self.assertIn("Ваши правила", self.get("/diagnostics"))

    @fixture.slow
    def test_rule_that_unlocks_nothing_says_so_instead_of_claiming_success(self):
        response = self.add(kind="brand", brand="Оленица", match="ОЛЕНИЦ")
        body = self.get(response.headers["location"])
        self.assertIn("не сдвинулся", body)
        self.assertIn("не хватает наблюдений", body)

    @fixture.slow
    def test_category_rule_reports_reclassified_positions(self):
        """Исправление отнесения главную меру не двигает — и это не бесполезность."""
        response = self.add(kind="category", group="vypechka", match="ШОК.*МОЛ")
        self.assertEqual(response.status_code, 303)
        body = self.get(response.headers["location"])
        self.assertIn("Переотнесено позиций", body)
        self.assertIn("исправление отнесения", body)

    @fixture.slow
    def test_user_rule_reaches_the_pipeline(self):
        """Правило доходит до разбора названий, а не оседает в базе.

        До ответа 8.6 оно при этом может не дойти: под окном сравнения покупки
        «ДСК ОГУРЦЫ» слишком старые (П-8). Проверяется то, что от правила
        действительно зависит, — разметка позиций.
        """
        before = self.get("/diagnostics/positions?search=ДСК ОГУРЦЫ")
        self.assertNotIn("<td>ДСК</td>", before)
        self.add(kind="brand", brand="ДСК", match="ДСК ОГУРЦЫ")
        after = self.get("/diagnostics/positions?search=ДСК ОГУРЦЫ")
        self.assertIn("<td>ДСК</td>", after)

    @fixture.slow
    def test_rollback_is_measured_too(self):
        self.add(kind="brand", brand="ДСК", match="ДСК ОГУРЦЫ")
        rule_id = self.rules()[0]["id"]
        response = self.client.post(f"/diagnostics/rules/{rule_id}/delete")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Что изменил откат правила", response.text)
        self.assertEqual(self.rules(), [])

    def test_broken_rule_is_refused_with_a_page(self):
        response = self.client.post("/diagnostics/rules",
                                    data={"kind": "brand", "brand": "X", "match": "["})
        self.assertEqual(response.status_code, 200)
        self.assertIn("не регулярное выражение", response.text)
        self.assertEqual(self.rules(), [])

    def test_rule_for_unknown_group_is_refused(self):
        response = self.client.post("/diagnostics/rules",
                                    data={"kind": "category", "group": "нетакой",
                                          "match": "X"})
        self.assertIn("которой нет", response.text)
        self.assertEqual(self.rules(), [])

    def test_empty_rule_is_refused(self):
        response = self.client.post("/diagnostics/rules",
                                    data={"kind": "brand", "brand": "", "match": ""})
        self.assertIn("Нужны и образец", response.text)
        self.assertEqual(self.rules(), [])

    @fixture.slow
    def test_duplicate_rule_is_refused(self):
        self.add(kind="brand", brand="ДСК", match="ДСК ОГУРЦЫ")
        response = self.client.post("/diagnostics/rules",
                                    data={"kind": "brand", "brand": "ДСК",
                                          "match": "ДСК ОГУРЦЫ"})
        self.assertIn("уже есть", response.text)
        self.assertEqual(len(self.rules()), 1)


if __name__ == "__main__":
    unittest.main()
