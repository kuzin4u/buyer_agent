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


#: Пробник взаимодействия: наведение и клик по легенде.
#:
#: События подаются в обработчик Chart.js собранными, а не через
#: `dispatchEvent`, и это не обход проверки, а единственный способ её провести.
#: У синтетического MouseEvent браузер приравнивает `offsetX` к `clientX`,
#: Chart.js берёт координату именно оттуда — и точка приходит смещённой на
#: положение холста в странице, то есть заведомо вне области графика. Проверка
#: тогда меряет не поведение графика, а особенность синтетики. Здесь событие
#: собирается ровно так, как его собирает платформа Chart.js из браузерного:
#: тип, координаты внутри холста и сам нативный объект. Всё остальное —
#: разбор попадания, подсказка, плагин легенды — настоящее.
INTERACT = """<!doctype html>
<html><head><meta charset="utf-8"><title>pending</title></head>
<body>
<iframe id="frame" src="%(path)s" style="width:1200px;height:900px;border:0"></iframe>
<script>
document.getElementById("frame").onload = function () {
  var frame = this;
  setTimeout(function () {
    try {
      var win = frame.contentWindow, doc = frame.contentDocument, out = {};
      var canvas = doc.getElementById("%(chart)s");
      var chart = win.Chart.getChart(canvas);

      function at(type, x, y) {
        return {type: type, chart: chart, x: x, y: y,
                native: new win.MouseEvent(type, {bubbles: true, view: win})};
      }
      function gap(a, b) {
        return Math.sqrt(Math.pow(a.x - b.x, 2) + Math.pow(a.y - b.y, 2));
      }

      out.datasets = chart.data.datasets.length;
      out.labels = chart.data.datasets.map(function (d) { return d.label; });

      /* Точка строго между двумя соседними узлами: если подсказка ловится
         только точным попаданием в узел, здесь её не будет.

         Расстояние меряется до узлов ВСЕХ рядов, а не только своего: линий
         восемь, они пересекаются, и середина своего отрезка запросто окажется
         вплотную к чужому узлу. Тогда проверка снова выродится в наведение на
         точку — просто на чужую. Берётся та середина, которая дальше всего от
         любого узла графика. */
      function visible(p) {
        return p && !isNaN(p.x) && !isNaN(p.y) && p.skip !== true;
      }
      var all = [];
      chart.data.datasets.forEach(function (_d, i) {
        chart.getDatasetMeta(i).data.filter(visible).forEach(function (p) {
          all.push({x: p.x, y: p.y});
        });
      });
      var mid = null, best = -1, onDataset = null, counted = 0;
      chart.data.datasets.forEach(function (_d, di) {
        var pts = chart.getDatasetMeta(di).data.filter(visible);
        counted += pts.length;
        for (var k = 0; k + 1 < pts.length; k++) {
          [0.25, 0.5, 0.75].forEach(function (t) {
            var candidate = {x: pts[k].x + (pts[k + 1].x - pts[k].x) * t,
                             y: pts[k].y + (pts[k + 1].y - pts[k].y) * t};
            var nearest = Infinity;
            all.forEach(function (p) {
              nearest = Math.min(nearest, gap(candidate, p));
            });
            if (nearest > best) {
              best = nearest; mid = candidate; onDataset = di;
            }
          });
        }
      });
      out.visiblePoints = counted;
      out.gapToNearestPoint = best;
      out.hoveredSegmentOf = onDataset;

      /* Что дал бы прежний режим: попадание строго в элемент. Разница между
         этими двумя числами и есть то, ради чего менялась настройка. */
      out.foundWithIntersect = chart.getElementsAtEventForMode(
        {native: true, x: mid.x, y: mid.y, type: "mousemove"},
        "nearest", {intersect: true, axis: "xy"}, false).length;

      chart._eventHandler(at("mousemove", mid.x, mid.y));
      out.activeOnLine = chart._active ? chart._active.length : 0;
      out.tooltipActive = chart.tooltip.getActiveElements().length;
      out.tooltipTitle = (chart.tooltip.title || []).join(" ");
      out.tooltipLines = (chart.tooltip.body || []).map(function (b) {
        return b.lines.join(" ");
      });

      /* Клик по названию в легенде — по второму ряду, если он есть. */
      var index = chart.data.datasets.length > 1 ? 1 : 0;
      var hit = chart.legend.legendHitBoxes[index];
      out.legendBoxes = chart.legend.legendHitBoxes.length;
      var cx = hit.left + hit.width / 2, cy = hit.top + hit.height / 2;

      out.colorsBefore = chart.data.datasets.map(function (d) {
        return d.borderColor; });
      chart._eventHandler(at("click", cx, cy));
      out.isolated = chart.$isolated;
      out.colorsAfter = chart.data.datasets.map(function (d) {
        return d.borderColor; });
      out.hiddenAfter = chart.data.datasets.map(function (d, i) {
        return !!chart.getDatasetMeta(i).hidden; });

      chart._eventHandler(at("click", cx, cy));
      out.isolatedAgain = chart.$isolated;
      out.colorsRestored = chart.data.datasets.map(function (d) {
        return d.borderColor; });

      document.title = "RESULT:" + encodeURIComponent(JSON.stringify(out));
    } catch (e) {
      document.title = "FAILED:" + encodeURIComponent(String((e && e.stack) || e));
    }
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

        @app_module.app.get("/__interact", response_class=HTMLResponse)
        def interact(target: str, chart: str):        # noqa: ANN001
            return INTERACT % {"path": target, "chart": chart}

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

    def interact(self, path, chart_id):
        """Измерения наведения и клика по легенде — из живого браузера."""
        target = f"http://127.0.0.1:{self.port}{path}"
        probe = (f"http://127.0.0.1:{self.port}/__interact"
                 f"?target={urllib.parse.quote(target, safe='')}"
                 f"&chart={urllib.parse.quote(chart_id, safe='')}")
        dom = subprocess.run(
            [self.chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--window-size=1280,900", "--virtual-time-budget=15000",
             "--dump-dom", probe],
            capture_output=True, text=True, timeout=120).stdout
        broken = re.search(r"<title>FAILED:([^<]*)</title>", dom)
        if broken:
            self.fail(f"{path}: пробник упал — "
                      f"{urllib.parse.unquote(broken.group(1))}")
        found = re.search(r"<title>RESULT:([^<]*)</title>", dom)
        self.assertIsNotNone(
            found, f"{path}: пробник не дошёл до результата")
        return json.loads(urllib.parse.unquote(found.group(1)))

    def test_tooltip_catches_the_line_not_only_the_point(self):
        """Подсказка ловится рядом с линией, а не точным попаданием в точку.

        На /prices линий восемь, и попасть мышью в точку радиусом три пикселя
        трудно. Проверяется именно это: курсор ставится СТРОГО МЕЖДУ двумя
        узлами, далеко от обоих, и подсказка обязана появиться, назвав величину
        и значение.
        """
        out = self.interact("/prices", "prices-chart")
        self.assertGreater(out["visiblePoints"], 1, "на линии меньше двух точек")
        self.assertGreater(
            out["gapToNearestPoint"], 20,
            "курсор оказался слишком близко к узлу — проверка выродилась в "
            "наведение на точку и больше ничего не стережёт")

        # Прежний режим на этом же месте не нашёл бы ничего — ради этой
        # разницы и менялась настройка.
        self.assertEqual(
            out["foundWithIntersect"], 0,
            "точка выбрана неудачно: в неё попадает и режим строгого попадания")

        self.assertEqual(out["activeOnLine"], 1,
                         "рядом с линией не нашлось ни одного ряда")
        self.assertEqual(out["tooltipActive"], 1, "подсказка не появилась")
        self.assertTrue(out["tooltipTitle"], "в подсказке нет подписи точки")
        self.assertTrue(out["tooltipLines"], "в подсказке нет строки значения")
        line = out["tooltipLines"][0]
        self.assertIn(":", line, f"подсказка без значения: {line!r}")
        name = line.split(":")[0]
        self.assertIn(name, out["labels"],
                      f"подсказка назвала не ряд графика: {line!r}")
        self.assertRegex(line.split(":", 1)[1], r"\d",
                         f"подсказка без числа: {line!r}")

    def test_legend_click_isolates_a_line_and_a_second_click_restores(self):
        """Клик по названию выделяет свой ряд и приглушает остальные.

        Ряд при этом не скрывается: скрытый меняет масштаб оси, и соседние
        линии прыгают, а вопрос у читателя — «которая из них моя».
        """
        out = self.interact("/prices", "prices-chart")
        self.assertGreater(out["datasets"], 1, "рядов меньше двух, выделять нечего")
        self.assertEqual(out["legendBoxes"], out["datasets"],
                         "в легенде не все ряды")
        self.assertEqual(out["isolated"], 1, "клик по легенде не выделил ряд")

        chosen = out["colorsAfter"][1]
        self.assertEqual(chosen, out["colorsBefore"][1],
                         "выделенный ряд сменил цвет")
        others = [c for i, c in enumerate(out["colorsAfter"]) if i != 1]
        for color in others:
            self.assertNotIn(color, out["colorsBefore"],
                             "остальные ряды не приглушены")
            self.assertEqual(len(color), 9,
                             f"приглушение не прозрачностью: {color}")
        self.assertFalse(any(out["hiddenAfter"]),
                         "ряд скрыт, а должен быть приглушён: масштаб оси "
                         "не должен меняться от выделения")

        self.assertIsNone(out["isolatedAgain"], "повторный клик не снял выделение")
        self.assertEqual(out["colorsRestored"], out["colorsBefore"],
                         "цвета не вернулись")

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

    def test_hover_and_legend_isolation_are_configured(self):
        """Дешёвая стража настроек, которые проверяет браузерный класс.

        Браузерный точнее, но требует Chrome и стоит секунды. Эти две строки
        держат главное: подсказка не требует точного попадания, а клик по
        легенде обрабатывается своим кодом, а не поведением по умолчанию.
        """
        script = self.read("web/static/charts.js")
        self.assertRegex(
            script, r"intersect:\s*false",
            "подсказка снова требует попадания в точку: в линию радиусом "
            "три пикселя мышью не попасть")
        self.assertRegex(
            script, r"onClick:[\s\S]{0,200}?isolate\(",
            "клик по легенде не ведёт в выделение ряда — значит вернулся к "
            "поведению по умолчанию, то есть к скрытию")
        self.assertIn(
            "legend: legendOptions", script,
            "легенда собирается мимо legendOptions, и обработчик к ней не "
            "прикреплён")

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
