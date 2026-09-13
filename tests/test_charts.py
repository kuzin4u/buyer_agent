"""Графики: проверка в настоящем браузере, а не по разметке.

**Зачем браузер.** Прежние проверки смотрели, что на странице есть `<canvas>` и
тег с данными, — и были зелёными в тот момент, когда график не работал вовсе.
Отказ был молчаливым и целиком браузерным: `Chart.js` в режиме
`responsive + maintainAspectRatio: false` берёт высоту у родителя, родитель
высоты не задавал и брал её у холста, каждый пересчёт добавлял ещё — холст
уезжал на 6519 пикселей вниз, а в окне оставалась пустая сетка. Ни разметка, ни
JSON, ни коды ответов об этом не знают: узнать это можно, только выполнив
страницу. Поэтому здесь поднимается настоящий сервер и настоящий Chrome.

**Что именно проверяется.** Не «нарисовалось красиво», а измеримое: график
построен, у холста разумная высота, в нём есть данные, и подписи оси идут по
возрастанию (иначе линия соединяет годы в случайном порядке и показывает
динамику, которой не было).

**Как достаётся результат.** Браузер открывает страницу-пробник, та грузит
проверяемую страницу в `iframe` того же происхождения, опрашивает `Chart` и
кладёт ответ в `document.title`, а `--dump-dom` печатает готовый DOM. Так
измерения приходят из живой страницы, и для этого не нужен ни драйвер, ни
внешняя зависимость.

Браузерный класс помечен `@slow`: пять запусков Chrome стоят 32 секунды, а
быстрый набор обязан оставаться быстрым. Структурные проверки ниже — те, что
стерегут ту же ошибку по исходникам, — быстрые и идут всегда. Если Chrome в
системе нет, браузерный класс пропускается: тогда остаются они.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fixture  # noqa: E402

try:
    import uvicorn                                    # noqa: F401
    WEB = True
except ImportError:                                   # оболочка не установлена
    WEB = False

REASON = ("веб-оболочка не установлена: pip install -r requirements.txt "
          "(ядро от неё не зависит, Р-6)")

#: Кандидаты на Chrome. Проверка без браузера невозможна, но и падать из-за его
#: отсутствия нельзя: ядро от браузера не зависит.
CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)

#: Потолок высоты холста. Разумный график укладывается в высоту обёртки
#: (320 px в app.css); всё, что заметно выше, — это и есть разъехавшийся холст.
MAX_CANVAS_HEIGHT = 700
MIN_CANVAS_HEIGHT = 80


def find_chrome():
    for path in CHROME_PATHS:
        if os.path.exists(path):
            return path
    return shutil.which("google-chrome") or shutil.which("chromium")


#: Страница-пробник. Живёт в тестах, а не в оболочке: это измерительный
#: инструмент, а не часть продукта.
PROBE = """<!doctype html>
<html><head><meta charset="utf-8"><title>pending</title></head>
<body>
<iframe id="frame" src="%s" style="width:1200px;height:900px;border:0"></iframe>
<script>
document.getElementById("frame").onload = function () {
  var frame = this;
  /* Chart.js рисует не в момент onload, а на следующем кадре: измеряем после
     паузы, иначе меряется ещё пустой холст. */
  setTimeout(function () {
    var win = frame.contentWindow, doc = frame.contentDocument, out = [];
    Array.prototype.forEach.call(doc.querySelectorAll("canvas"), function (c) {
      var chart = null;
      try { chart = win.Chart && win.Chart.getChart(c); } catch (e) {}
      var box = c.getBoundingClientRect(), labels = [];
      try { labels = chart.scales.x.getLabels(); } catch (e) {}
      out.push({
        id: c.id,
        built: !!chart,
        width: Math.round(box.width),
        height: Math.round(box.height),
        datasets: chart ? chart.data.datasets.length : 0,
        points: chart ? chart.data.datasets.reduce(
          function (n, ds) { return n + ds.data.length; }, 0) : 0,
        labels: labels
      });
    });
    document.title = "RESULT:" + encodeURIComponent(JSON.stringify(out));
  }, 800);
};
</script>
</body></html>
"""


@fixture.slow
@unittest.skipUnless(WEB, REASON)
class ChartsInBrowserTest(unittest.TestCase):
    """Один сервер и один браузер на класс: и то и другое стоит секунды."""

    @classmethod
    def setUpClass(cls):
        cls.chrome = find_chrome()
        if not cls.chrome:
            raise unittest.SkipTest("Chrome не найден: браузерная проверка "
                                    "графиков пропущена")

        cls.dir = tempfile.mkdtemp()
        os.environ["BUYER_AGENT_DB"] = os.path.join(cls.dir, "state.db")

        import uvicorn
        import web.app as app_module
        from fastapi.responses import HTMLResponse

        cls.module = app_module
        cls.module.reset_state()

        # Пробник монтируется в то же приложение намеренно: `iframe` читает
        # чужую страницу только при совпадении происхождения, а другой порт —
        # уже другое происхождение.
        @app_module.app.get("/__probe", response_class=HTMLResponse)
        def probe(target: str):                       # noqa: ANN001
            return PROBE % target

        # Порт занимается заранее и передаётся серверу готовым сокетом: выбрать
        # свободный порт и потом отдать его строкой — значит оставить зазор,
        # в который успевает влезть кто-то ещё.
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        cls.port = sock.getsockname()[1]
        config = uvicorn.Config(app_module.app, log_level="warning")
        cls.server = uvicorn.Server(config)
        cls.thread = threading.Thread(
            target=cls.server.run, kwargs={"sockets": [sock]}, daemon=True)
        cls.thread.start()

        deadline = time.time() + 60
        while not cls.server.started:
            if time.time() > deadline:
                raise AssertionError("сервер не поднялся за 60 с")
            time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        server = getattr(cls, "server", None)
        if server is not None:
            server.should_exit = True
            cls.thread.join(timeout=10)
        if hasattr(cls, "module"):
            cls.module.reset_state()
        os.environ.pop("BUYER_AGENT_DB", None)

    def charts(self, path):
        """Измерения всех графиков страницы — из живого браузера."""
        target = f"http://127.0.0.1:{self.port}{path}"
        probe = (f"http://127.0.0.1:{self.port}/__probe"
                 f"?target={urllib.parse.quote(target, safe='')}")
        dom = subprocess.run(
            [self.chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--window-size=1280,900", "--virtual-time-budget=15000",
             "--dump-dom", probe],
            capture_output=True, text=True, timeout=120).stdout

        found = re.search(r"<title>RESULT:([^<]*)</title>", dom)
        self.assertIsNotNone(
            found, f"{path}: пробник не дошёл до результата — страница не "
                   f"выполнилась или упала на ошибке JS")
        return json.loads(urllib.parse.unquote(found.group(1)))

    def assert_drawn(self, path, expected_id):
        charts = self.charts(path)
        ids = [c["id"] for c in charts]
        self.assertIn(expected_id, ids, f"{path}: холста нет на странице")
        chart = [c for c in charts if c["id"] == expected_id][0]

        self.assertTrue(chart["built"],
                        f"{path}: холст есть, а графика на нём нет — "
                        f"Chart.js не построился")
        self.assertGreater(chart["datasets"], 0, f"{path}: график без рядов")
        self.assertGreater(chart["points"], 0, f"{path}: график без точек")
        self.assertGreater(chart["width"], 200, f"{path}: холст шириной в ноль")
        # Обе границы важны: ноль — график не виден, тысячи — он уехал за экран.
        self.assertGreater(
            chart["height"], MIN_CANVAS_HEIGHT,
            f"{path}: холст высотой {chart['height']} px — графика не видно")
        self.assertLess(
            chart["height"], MAX_CANVAS_HEIGHT,
            f"{path}: холст высотой {chart['height']} px разъехался — "
            f"обёртка .chart не задаёт высоту, и Chart.js наращивает её сам")
        return chart

    def test_prices_chart_is_actually_drawn(self):
        self.assert_drawn("/prices", "prices-chart")

    def test_spending_chart_is_actually_drawn(self):
        self.assert_drawn("/spending?by=year", "spending-chart")

    def test_growth_chart_is_actually_drawn(self):
        self.assert_drawn("/spending?by=year&growth=true&months=6",
                          "growth-chart")

    def test_venues_chart_is_actually_drawn(self):
        self.assert_drawn("/venues", "venues-chart")

    def test_price_years_go_in_order(self):
        """Годы на оси обязаны идти по возрастанию.

        Категорийная ось расставляет подписи в порядке первого появления в
        рядах, а ряды приходят отсортированными по рублю, не по годам. Получался
        порядок 2019, 2021, 2023, 2025, 2024 — и линия, соединяя точки в этом
        порядке, показывала динамику, которой не было.
        """
        labels = self.assert_drawn("/prices", "prices-chart")["labels"]
        self.assertGreater(len(labels), 1, "на оси меньше двух лет")
        years = [int(label) for label in labels]
        self.assertEqual(years, sorted(years),
                         f"годы на оси идут не по порядку: {labels}")


@unittest.skipUnless(WEB, REASON)
class ChartMarkupTest(unittest.TestCase):
    """Быстрая стража тех же условий — по исходникам, без браузера.

    Идут в каждом прогоне, и не только потому, что дешевле. Браузерный класс их
    не покрывает: если снять высоту из `.chart`, Chart.js берёт запасные 150 px,
    график остаётся нарисованным и измеримо исправным — приплюснутым втрое, — и
    браузерная проверка проходит. Она стережёт разъезд, эти — объявленную
    высоту (Р-28).
    """

    def read(self, path):
        with open(os.path.join(BASE, path), encoding="utf-8") as f:
            return f.read()

    def templates(self):
        folder = os.path.join(BASE, "web", "templates")
        for name in sorted(os.listdir(folder)):
            if name.endswith(".html"):
                yield name, self.read(os.path.join("web", "templates", name))

    def test_every_canvas_sits_in_a_sized_box(self):
        """Высоту графику задаёт обёртка — иначе холст растёт сам собой."""
        for name, markup in self.templates():
            for match in re.finditer(r'<canvas[^>]*id="([^"]+)"[^>]*>', markup):
                before = markup[:match.start()]
                self.assertTrue(
                    before.rstrip().endswith('<div class="chart">'),
                    f"{name}: холст {match.group(1)} не обёрнут в "
                    f'<div class="chart"> — Chart.js будет наращивать высоту, '
                    f"пока график не уедет за экран")
                self.assertNotIn(
                    "height=", match.group(0),
                    f"{name}: высота на самом холсте ничего не задаёт в режиме "
                    f"responsive, её задаёт обёртка .chart")

    def test_the_chart_box_has_a_height_in_css(self):
        css = self.read("web/static/app.css")
        rule = re.search(r"\.chart\s*\{([^}]*)\}", css)
        self.assertIsNotNone(rule, "в app.css нет правила .chart")
        self.assertRegex(rule.group(1), r"height:\s*\d+",
                         ".chart без высоты в пикселях — холст разъедется")
        self.assertIn("position: relative", rule.group(1),
                      "Chart.js требует position: relative у контейнера")

    def test_every_canvas_is_known_to_the_drawing_code(self):
        """Холст без кода отрисовки — пустое место на странице."""
        script = self.read("web/static/charts.js")
        for name, markup in self.templates():
            for found in re.finditer(r'<canvas[^>]*id="([^"]+)"', markup):
                self.assertIn(found.group(1), script,
                              f"{name}: {found.group(1)} никто не рисует")

    def test_pages_do_not_print_python_objects(self):
        """Repr питоновского объекта на странице — всегда ошибка шаблона.

        Так `{{ summary.keys }}` печатал «<built-in method keys of dict object
        at 0x...»: у словаря Jinja берёт сначала атрибут и только потом ключ.
        """
        from fastapi.testclient import TestClient
        folder = tempfile.mkdtemp()
        os.environ["BUYER_AGENT_DB"] = os.path.join(folder, "state.db")
        import web.app as app_module
        app_module.reset_state()
        try:
            client = TestClient(app_module.app)
            for path in ("/prices", "/spending?by=year", "/venues",
                         "/spending?by=year&growth=true&months=6",
                         "/profile", "/basket?period=week", "/lapsed",
                         "/budget?amount=2000", "/choose", "/diagnostics"):
                body = client.get(path).text
                for leak in ("<built-in method", "object at 0x",
                             "<bound method", "<function "):
                    self.assertNotIn(leak, body, f"{path}: {leak}")
        finally:
            app_module.reset_state()
            os.environ.pop("BUYER_AGENT_DB", None)


if __name__ == "__main__":
    unittest.main()
