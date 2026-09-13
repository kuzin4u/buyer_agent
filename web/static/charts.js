/* Графики. Данные приходят из ядра готовыми числами — здесь только рисование.
   Chart.js лежит локально (web/static/vendor): инструмент не должен зависеть от
   сети, чтобы нарисовать график. Если скрипт не загрузился, таблица на странице
   остаётся — она и есть источник правды, график лишь её пересказ. */
(function () {
  if (typeof Chart === "undefined") return;

  var INK = "#1a1a18", MUTED = "#6d6a63", LINE = "#e2dfd8";
  var PALETTE = ["#2f5d50", "#9b3030", "#8a5a1b", "#3b5e8c", "#6b4b7a",
                 "#2f6b3a", "#a06030", "#4a6b6b"];

  /* Прозрачность приглушённого ряда: восьмизначный hex, последние два знака —
     альфа. 0x26 — это 15%: ряд виден как контекст, но не спорит с выделенным. */
  var DIM = "26";

  function data(id) {
    var tag = document.getElementById(id);
    if (!tag) return null;
    try { return JSON.parse(tag.textContent); } catch (e) { return null; }
  }

  /* Разряды пробелом — как в таблицах на странице. Форматирование, не счёт:
     число приходит посчитанным ядром и здесь только печатается. */
  function money(value) {
    if (value === null || value === undefined) return "—";
    return Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 2 });
  }

  function dim(color) {
    return (typeof color === "string" && color.charAt(0) === "#"
            && color.length === 7) ? color + DIM : color;
  }

  /* Исходные цвета запоминаются один раз: приглушение перезаписывает поля
     набора, и без снимка вернуть прежний вид уже нечем.

     Признак «снимок снят» отдельный, а не «поле цвета непустое». Так и было
     сперва — проверялось `$border === undefined`, — и на 8.6 с 8.8 подсветка
     не снималась совсем: у столбиковых наборов рамка не задана, признак
     оставался пустым навсегда, снимок пересчитывался при каждой перерисовке и
     на втором заходе запоминал уже ПРИГЛУШЁННЫЕ цвета. Возвращать становилось
     некуда. Поймано проверкой в браузере, разметка об этом знать не могла. */
  function remember(chart) {
    chart.data.datasets.forEach(function (ds) {
      if (!ds.$saved) {
        ds.$saved = true;
        ds.$border = ds.borderColor;
        ds.$background = ds.backgroundColor;
        ds.$width = ds.borderWidth;
      }
    });
  }

  /* Выделение бывает двух видов, потому что строка таблицы означает разное.

     На 8.5 строка — это ТОВАР, и товару отвечает целая линия: вид "dataset".
     На 8.6 и 8.8 строка — тоже товар или разрез, но на графике ему отвечает
     один столбик в каждом ряду: ряды там — магазины и периоды. Вид "index".

     Путать их нельзя: приглушив на 8.6 «все ряды кроме первого», мы спрячем
     все магазины кроме одного — то есть ответим на вопрос, которого не
     задавали. */
  var DATASET = "dataset", INDEX = "index";

  function same(a, b) {
    return (a === b) || (a && b && a.kind === b.kind && a.at === b.at);
  }

  /* Что сейчас выделено: наведение временно перебивает закреплённое кликом. */
  function focus(chart) {
    return chart.$hot || chart.$pin || null;
  }

  function paint(chart) {
    remember(chart);
    var on = focus(chart);
    chart.data.datasets.forEach(function (ds, di) {
      if (!on) {
        ds.borderColor = ds.$border;
        ds.backgroundColor = ds.$background;
        ds.borderWidth = ds.$width;
      } else if (on.kind === DATASET) {
        var mine = (di === on.at);
        ds.borderColor = mine ? ds.$border : dim(ds.$border);
        ds.backgroundColor = mine ? ds.$background : dim(ds.$background);
        ds.borderWidth = mine ? (ds.$width || 2) + 1 : ds.$width;
      } else {
        /* Цвет по точкам: выделяется столбик, а не ряд целиком. */
        ds.borderColor = ds.$border;
        ds.borderWidth = ds.$width;
        ds.backgroundColor = ds.data.map(function (_value, i) {
          return (i === on.at) ? ds.$background : dim(ds.$background);
        });
      }
    });
    /* Без анимации: подсветка при наведении обязана поспевать за курсором, а
       не догонять его.

       Последнее событие на время перерисовки забывается, и это обязательно.
       `update()` ПЕРЕИГРЫВАЕТ его — так Chart.js держит подсветку верной, когда
       меняются данные. У нас данные не меняются, меняются только цвета, и
       переигровка возвращает ровно то наведение, которое мы сейчас снимаем:
       курсор ушёл с холста, подсветку сняли, перерисовали — и она вернулась.
       Замерено в браузере: без этих трёх строк `$hot` после ухода курсора
       остаётся прежним. */
    var pending = chart._lastEvent;
    chart._lastEvent = null;
    chart.update("none");
    chart._lastEvent = pending;
    if (chart.$rows) {
      chart.$rows.forEach(function (row, i) {
        row.classList.toggle("lit", !!on && i === on.at);
        row.classList.toggle("pinned",
          !!chart.$pin && i === chart.$pin.at);
      });
    }
  }

  function hover(chart, spot) {
    if (same(chart.$hot, spot)) return;
    chart.$hot = spot;
    paint(chart);
  }

  function pin(chart, spot) {
    chart.$pin = same(chart.$pin, spot) ? null : spot;
    paint(chart);
  }

  /* Клик по названию в легенде выделяет свой ряд и приглушает остальные,
     повторный клик возвращает всё как было.

     Поведение по умолчанию (скрыть ряд) заменено намеренно: на /prices линий
     восемь, и вопрос у читателя не «убрать лишние», а «которая из них моя».
     Скрытый ряд к тому же меняет масштаб оси, и соседние линии прыгают —
     приглушённый не меняет ничего, кроме заметности. */
  function isolate(chart, index) {
    pin(chart, { kind: DATASET, at: index });
  }

  function legendOptions(extra) {
    var base = {
      labels: { color: MUTED, boxWidth: 12, font: { size: 11 } },
      onClick: function (event, item, legend) {
        isolate(legend.chart, item.datasetIndex);
      }
    };
    for (var key in (extra || {})) base[key] = extra[key];
    return base;
  }

  /* Строка таблицы ↔ ряд графика.

     Таблица — источник правды, график её пересказ (Р-23), и связь нужна именно
     в эту сторону: человек читает строку и хочет найти её линию. Обратный ход
     тоже есть — наведение на линию подсвечивает строку, — но он вторичен.

     Строки, которым на графике ничего не отвечает, не размечаются вовсе: на
     8.5 в таблице двадцать рядов, а на графике восемь. Кликать по строке,
     которая ничего не выделит, — это молчаливый отказ, а он неотличим от
     поломки. Размечены только те, у кого есть пара. */
  function linkTable(chart, tableId, kind) {
    var table = document.getElementById(tableId);
    if (!table) return;
    var rows = Array.prototype.slice.call(
      table.querySelectorAll("tr[data-series]"));
    if (!rows.length) return;
    chart.$rows = rows;
    /* Уход курсора С ХОЛСТА снимает подсветку.

       Своими силами Chart.js этого не делает: onHover он вызывает, только пока
       точка внутри области графика, а у события ухода координат нет вовсе. Без
       этого подсветка залипает — курсор ушёл, а приглушённой остаётся вся
       картинка, кроме линии, на которую он смотрел последней.

       Слушается именно `mouseout`, а не `mouseleave`, и это не вкусовщина.
       Chart.js слушает `mouseout` сам и по нему забывает последнее событие. А
       забыть его обязательно: `update()` ПЕРЕИГРЫВАЕТ последнее событие, то
       есть наша же перерисовка тут же вернула бы подсветку обратно. Порядок
       выходит верный сам собой — обработчик Chart.js подписан раньше нашего. */
    chart.canvas.addEventListener("mouseout", function () {
      hover(chart, null);
    });
    rows.forEach(function (row, i) {
      var spot = { kind: kind, at: i };
      row.classList.add("linked");
      row.addEventListener("mouseenter", function () { hover(chart, spot); });
      row.addEventListener("mouseleave", function () { hover(chart, null); });
      row.addEventListener("click", function () { pin(chart, spot); });
    });
  }

  /* Наведение на график подсвечивает строку — тем же механизмом, что и
     обратный ход, поэтому подсветка не может разъехаться между ними. */
  function onHover(kind) {
    return function (event, actives, chart) {
      if (!chart.$rows) return;
      if (!actives || !actives.length) {
        hover(chart, null);
        return;
      }
      var element = actives[0];
      var at = (kind === DATASET) ? element.datasetIndex : element.index;
      hover(chart, (at < chart.$rows.length) ? { kind: kind, at: at } : null);
    };
  }

  /* Наведение. `intersect: false` — главное здесь: без него подсказка ловится
     только точным попаданием в точку, а попасть в точку радиусом 3 пикселя
     мышью трудно. С ним берётся ближайший элемент, то есть достаточно подвести
     курсор к линии. `mode: nearest` — именно ближайший ряд, а не все сразу:
     вопрос читателя «что это за линия», и ответ должен быть про одну. */
  function options(extra) {
    extra = extra || {};
    var out = {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "nearest", intersect: false, axis: "xy" },
      hover: { mode: "nearest", intersect: false },
      onHover: onHover(extra.link || DATASET),
      plugins: {
        legend: legendOptions(extra.legend),
        tooltip: {
          mode: "nearest", intersect: false,
          backgroundColor: INK, titleFont: { size: 12 },
          bodyFont: { size: 12 }, padding: 8, displayColors: true,
          callbacks: {
            label: function (ctx) {
              var value = (ctx.parsed && ctx.parsed.y !== undefined)
                ? ctx.parsed.y : ctx.raw;
              return (ctx.dataset.label || "") + ": " + money(value);
            }
          }
        }
      },
      scales: {
        x: { ticks: { color: MUTED, font: { size: 11 } }, grid: { color: LINE } },
        y: { ticks: { color: MUTED, font: { size: 11 } }, grid: { color: LINE } }
      }
    };
    if (extra && extra.scales) {
      for (var axis in extra.scales) out.scales[axis] = extra.scales[axis];
    }
    if (extra && extra.parsing) out.parsing = extra.parsing;
    return out;
  }

  /* 8.5: ряд цен по годам, отдельная линия на товар. Единицы у рядов разные,
     поэтому они подписаны в легенде — складывать их нельзя. */
  var prices = data("prices-data");
  if (prices && prices.length) {
    /* Ось строит ядро, а не Chart.js: на категорийной оси категории идут в
       порядке ПЕРВОГО появления в рядах, и годы выстраиваются как попало —
       2019, 2021, 2023, 2025, 2024. Линия при этом соединяет точки в этом же
       порядке, и график показывает динамику, которой не было. Общий список
       годов собираем сами и сортируем числом. */
    var years = [];
    prices.forEach(function (row) {
      row.points.forEach(function (point) {
        if (years.indexOf(point.x) === -1) years.push(point.x);
      });
    });
    years.sort(function (a, b) { return Number(a) - Number(b); });

    var pricesChart = new Chart(document.getElementById("prices-chart"), {
      type: "line",
      data: {
        labels: years,
        datasets: prices.map(function (row, i) {
          return {
            label: row.label + " (" + row.unit + ")",
            data: row.points,
            borderColor: PALETTE[i % PALETTE.length],
            backgroundColor: PALETTE[i % PALETTE.length],
            tension: 0.2, borderWidth: 2, pointRadius: 3,
            /* Зона попадания шире самой точки: курсору не обязательно
               останавливаться ровно на ней. */
            pointHoverRadius: 6, pointHitRadius: 12
          };
        })
      },
      options: options({
        link: DATASET,
        parsing: { xAxisKey: "x", yAxisKey: "y" },
        scales: {
          x: { type: "category", ticks: { color: MUTED }, grid: { color: LINE } }
        }
      })
    });
    linkTable(pricesChart, "prices-table", DATASET);
  }

  /* 8.6: один товар — столбик на магазин. */
  var venues = data("venues-data");
  if (venues && venues.length) {
    var shops = [];
    venues.forEach(function (row) {
      row.bars.forEach(function (b) {
        if (shops.indexOf(b.venue) === -1) shops.push(b.venue);
      });
    });
    var venuesChart = new Chart(document.getElementById("venues-chart"), {
      type: "bar",
      data: {
        labels: venues.map(function (r) { return r.label; }),
        datasets: shops.map(function (shop, i) {
          return {
            label: shop,
            backgroundColor: PALETTE[i % PALETTE.length],
            data: venues.map(function (row) {
              var hit = row.bars.filter(function (b) { return b.venue === shop; })[0];
              return hit ? hit.median : null;
            })
          };
        })
      },
      options: options({ link: INDEX })
    });
    linkTable(venuesChart, "venues-table", INDEX);
  }

  /* 8.8: траты в разрезе. Ряд один, выделять нечего — легенда не нужна. */
  var spending = data("spending-data");
  if (spending && spending.length) {
    var spendingChart = new Chart(document.getElementById("spending-chart"), {
      type: "bar",
      data: {
        labels: spending.map(function (r) { return r.key; }),
        datasets: [{ label: "₽", data: spending.map(function (r) { return r.amount; }),
                     backgroundColor: PALETTE[0] }]
      },
      options: options({ legend: { display: false }, link: INDEX })
    });
    linkTable(spendingChart, "spending-table", INDEX);
  }

  /* 8.8: окно против предыдущего окна — два столбика рядом. */
  var growth = data("growth-data");
  if (growth && growth.length) {
    var growthChart = new Chart(document.getElementById("growth-chart"), {
      type: "bar",
      data: {
        labels: growth.map(function (r) { return r.key; }),
        datasets: [
          { label: "было", data: growth.map(function (r) { return r.before; }),
            backgroundColor: LINE, borderColor: MUTED, borderWidth: 1 },
          { label: "стало", data: growth.map(function (r) { return r.after; }),
            backgroundColor: PALETTE[0] }
        ]
      },
      options: options({ link: INDEX })
    });
    linkTable(growthChart, "growth-table", INDEX);
  }
})();
