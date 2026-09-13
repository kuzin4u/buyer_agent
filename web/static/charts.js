/* Графики. Данные приходят из ядра готовыми числами — здесь только рисование.
   Chart.js лежит локально (web/static/vendor): инструмент не должен зависеть от
   сети, чтобы нарисовать график. Если скрипт не загрузился, таблица на странице
   остаётся — она и есть источник правды, график лишь её пересказ. */
(function () {
  if (typeof Chart === "undefined") return;

  var INK = "#1a1a18", MUTED = "#6d6a63", LINE = "#e2dfd8";
  var PALETTE = ["#2f5d50", "#9b3030", "#8a5a1b", "#3b5e8c", "#6b4b7a",
                 "#2f6b3a", "#a06030", "#4a6b6b"];

  function data(id) {
    var tag = document.getElementById(id);
    if (!tag) return null;
    try { return JSON.parse(tag.textContent); } catch (e) { return null; }
  }

  var common = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { labels: { color: MUTED, boxWidth: 12, font: { size: 11 } } } },
    scales: {
      x: { ticks: { color: MUTED, font: { size: 11 } }, grid: { color: LINE } },
      y: { ticks: { color: MUTED, font: { size: 11 } }, grid: { color: LINE } }
    }
  };

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
            tension: 0.2, borderWidth: 2, pointRadius: 3
          };
        })
      },
      options: Object.assign({}, common, {
        parsing: { xAxisKey: "x", yAxisKey: "y" },
        scales: Object.assign({}, common.scales, {
          x: { type: "category", ticks: { color: MUTED }, grid: { color: LINE } }
        })
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
      options: common
    });
  }

  /* 8.8: траты в разрезе. */
  var spending = data("spending-data");
  if (spending && spending.length) {
    new Chart(document.getElementById("spending-chart"), {
      type: "bar",
      data: {
        labels: spending.map(function (r) { return r.key; }),
        datasets: [{ label: "₽", data: spending.map(function (r) { return r.amount; }),
                     backgroundColor: PALETTE[0] }]
      },
      options: Object.assign({}, common, {
        plugins: { legend: { display: false } }
      })
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
      options: common
    });
  }
})();
