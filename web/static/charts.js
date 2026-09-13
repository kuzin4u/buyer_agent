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
     набора, и без снимка вернуть прежний вид уже нечем. */
  function remember(chart) {
    chart.data.datasets.forEach(function (ds) {
      if (ds.$border === undefined) {
        ds.$border = ds.borderColor;
        ds.$background = ds.backgroundColor;
        ds.$width = ds.borderWidth;
      }
    });
  }

  /* Клик по названию в легенде выделяет свой ряд и приглушает остальные,
     повторный клик возвращает всё как было.

     Поведение по умолчанию (скрыть ряд) заменено намеренно: на /prices линий
     восемь, и вопрос у читателя не «убрать лишние», а «которая из них моя».
     Скрытый ряд к тому же меняет масштаб оси, и соседние линии прыгают —
     приглушённый не меняет ничего, кроме заметности. */
  function isolate(chart, index) {
    remember(chart);
    var on = (chart.$isolated === index) ? null : index;
    chart.$isolated = on;
    chart.data.datasets.forEach(function (ds, i) {
      var muted = (on !== null && i !== on);
      ds.borderColor = muted ? dim(ds.$border) : ds.$border;
      ds.backgroundColor = muted ? dim(ds.$background) : ds.$background;
      ds.borderWidth = (on === i) ? (ds.$width || 2) + 1 : ds.$width;
    });
    chart.update();
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

  /* Наведение. `intersect: false` — главное здесь: без него подсказка ловится
     только точным попаданием в точку, а попасть в точку радиусом 3 пикселя
     мышью трудно. С ним берётся ближайший элемент, то есть достаточно подвести
     курсор к линии. `mode: nearest` — именно ближайший ряд, а не все сразу:
     вопрос читателя «что это за линия», и ответ должен быть про одну. */
  function options(extra) {
    var out = {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "nearest", intersect: false, axis: "xy" },
      hover: { mode: "nearest", intersect: false },
      plugins: {
        legend: legendOptions((extra || {}).legend),
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

    new Chart(document.getElementById("prices-chart"), {
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
        parsing: { xAxisKey: "x", yAxisKey: "y" },
        scales: {
          x: { type: "category", ticks: { color: MUTED }, grid: { color: LINE } }
        }
      })
    });
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
    new Chart(document.getElementById("venues-chart"), {
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
      options: options()
    });
  }

  /* 8.8: траты в разрезе. Ряд один, выделять нечего — легенда не нужна. */
  var spending = data("spending-data");
  if (spending && spending.length) {
    new Chart(document.getElementById("spending-chart"), {
      type: "bar",
      data: {
        labels: spending.map(function (r) { return r.key; }),
        datasets: [{ label: "₽", data: spending.map(function (r) { return r.amount; }),
                     backgroundColor: PALETTE[0] }]
      },
      options: options({ legend: { display: false } })
    });
  }

  /* 8.8: окно против предыдущего окна — два столбика рядом. */
  var growth = data("growth-data");
  if (growth && growth.length) {
    new Chart(document.getElementById("growth-chart"), {
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
      options: options()
    });
  }
})();
