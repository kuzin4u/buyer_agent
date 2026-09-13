#!/usr/bin/env python3
"""Командная строка агента-закупщика — функции ядра А на уровне группы.

    python3 cli.py profile                     8.1 профиль предпочтений
    python3 cli.py basket --period week        8.2 корзина «как обычно»
    python3 cli.py basket --budget 2000        8.2 то же под заданную сумму
    python3 cli.py budget 2000                 8.3 уложиться в сумму с заменами
    python3 cli.py lapsed                      8.4 что давно не покупал
    python3 cli.py prices                      8.5 динамика цен внутри SKU
    python3 cli.py venues                      8.6 где что дешевле
    python3 cli.py venues --basket week        8.6 умная корзина: маршрут экономии
    python3 cli.py choose                      8.7 выбор магазина
    python3 cli.py spending --by dept          8.8 контроль трат
    python3 cli.py spending --growth --by group   где траты растут

Это временная оболочка: интерфейс — веб и бот, они в С5 и С6 (DECISIONS Р-1).
Числа здесь считает ядро, CLI только печатает.
"""

import argparse
import sys
from collections import Counter
from functools import lru_cache

from agent.config import Config, dataset_path
from agent.history import PRICE_WINDOW_MONTHS
from agent.settings import Settings
from agent.adapters.receipts_fns import Pipeline, load_receipts
from agent.adapters.receipts_fns.pipeline import to_history
from agent import intake as I
from agent import matching as M
from agent import profile as P

WIDTH = 64
#: как показывать базовую единицу цены; складывать числа разных единиц нельзя
UNIT = {"kg": "₽/кг", "l": "₽/л", "pcs": "₽/шт"}


def money(value):
    return f"{value:,.0f}".replace(",", " ")


def signed(value):
    """Со знаком: изменение трат читается только вместе с направлением."""
    return f"{value:+,.0f}".replace(",", " ")


def head(title):
    print("=" * WIDTH)
    print(title)
    print("=" * WIDTH)


@lru_cache(maxsize=None)
def _load(data, candidates):
    settings = Settings.load()
    if candidates:
        settings.include_candidates = True
    # Рабочий корпус: эталонный датасет плюс принятые выгрузки. Явный --data
    # перекрывает и то и другое — он для разбора чужого файла, а не для работы.
    receipts = (load_receipts(data) if data
                else list(I.build().receipts))
    run = Pipeline(Config.load(),
                   include_candidates=settings.include_candidates).run(receipts)
    history = to_history(run)
    return history, settings, P.build(history, settings)


def load(args):
    """Прогон конвейера. Кэшируется: в одном процессе он не должен повторяться."""
    return _load(args.data, bool(args.candidates))


def cmd_profile(args):
    history, settings, prof = load(args)
    head("8.1 ПРОФИЛЬ ПРЕДПОЧТЕНИЙ")
    print(f"  период:          {prof.span[0][:10]} … {prof.span[1][:10]}  "
          f"({prof.months:.0f} мес)")
    print(f"  продуктовых сделок: {prof.txns}, на {money(prof.spend)} ₽")
    print(f"  средний чек:     {money(prof.avg_txn)} ₽")
    print(f"  основной магазин: {prof.main_venue} — {prof.main_venue_share:.0%} "
          f"трат, привязанных к магазину")
    print(f"  постоянная корзина: {len(prof.staples)} групп")

    print("\n  Постоянная корзина — берётся не реже раза в месяц:")
    print(f"    {'группа':14}{'сделок':>8}{'раз/мес':>9}{'интервал':>10}"
          f"{'потрачено':>12}  типичный")
    for stat in prof.staple_stats():
        gap = f"{stat.median_gap_days:.0f} дн" if stat.median_gap_days else "—"
        label = stat.typical_key.label if stat.typical_key else "—"
        print(f"    {stat.group:14}{stat.txns:8}{stat.per_month:9.2f}{gap:>10}"
              f"{money(stat.amount):>12}  {label[:24]}")

    print("\n  Доли трат по отделам (внутри продуктовых):")
    for dept, share in sorted(prof.dept_shares.items(), key=lambda x: -x[1]):
        print(f"    {dept:24}{share:6.1%}")

    print("\n  Магазины:")
    for venue in sorted(prof.venues.values(), key=lambda v: -v.amount):
        print(f"    {venue.venue:20}{venue.txns:5} сделок {money(venue.amount):>11} ₽"
              f"{venue.share:7.1%}")


def cmd_basket(args):
    _history, settings, prof = load(args)
    if args.budget:
        basket = P.for_average_txn(prof, budget=args.budget, settings=settings)
        title = f"8.2 КОРЗИНА ПОД {money(args.budget)} ₽"
    elif args.period:
        basket = P.for_period(prof, args.period, settings)
        title = f"8.2 КОРЗИНА «КАК ОБЫЧНО» — {args.period}"
    else:
        basket = P.for_average_txn(prof, settings=settings)
        title = f"8.2 КОРЗИНА «КАК ОБЫЧНО» — средний чек {money(prof.avg_txn)} ₽"

    head(title)
    for dept, lines in basket.by_dept().items():
        print(f"\n  {dept}")
        for line in lines:
            mark = " *" if line.reason == "обязательная" else "  "
            print(f"   {mark}{line.group:14}×{line.qty:<3}{money(line.amount):>8} ₽"
                  f"   {line.label[:30]}")
    print(f"\n  Итого: {len(basket)} позиций, {money(basket.total)} ₽")
    if any(x.reason == "обязательная" for x in basket.lines):
        print("  * — обязательная позиция из ваших настроек")
    if args.budget:
        print("\n  Это 8.2: состав «как обычно», обрезанный по сумме. Уложиться в")
        print(f"  сумму, заменяя дорогое на дешёвое внутри группы, — "
              f"`budget {money(args.budget)}` (8.3).")


def cmd_budget(args):
    history, settings, prof = load(args)
    catalog = M.from_history(history)
    f = M.fit(prof, catalog, args.amount, period=args.period, settings=settings)

    head(f"8.3 КОРЗИНА ПОД {money(args.amount)} ₽ — {args.period}")
    print(f"  как обычно:{money(f.total_before):>12} ₽")
    if f.swaps:
        print(f"  заменами:  {signed(-f.saved_by_swaps):>12} ₽   "
              f"замен: {len(f.swaps)}")
    if f.dropped:
        print(f"  выброшено: {signed(-f.saved_by_drops):>12} ₽   "
              f"групп: {len(f.dropped)}")
    print(f"  итого:     {money(f.total):>12} ₽", end="")
    if f.empty:
        cheapest = f.cheapest_dropped
        print("   КОРЗИНА ПУСТА")
        print(f"\n  В эту сумму не входит ничего: самая дешёвая регулярная группа — "
              f"{cheapest.group}")
        print(f"  за {money(cheapest.amount)} ₽. Бюджет закрылся тем, что покупать "
              f"нечего, а это не ответ.")
        print(f"  Посмотрите корзину на {args.period} целиком: "
              f"`basket --period {args.period}`.")
        return
    if f.fits:
        print(f"   запас {money(f.slack)} ₽")
    else:
        print(f"   НЕ УЛОЖИЛИСЬ: не хватает {money(-f.slack)} ₽")
        print("\n  Дальше сжимать нечем: в корзине остались только обязательные")
        print("  позиции из ваших настроек, а их агент не выбрасывает (§3.3).")

    if f.swaps:
        print("\n  Чем заменили — потребность та же, товар другой:")
        for line in sorted((x for x in f.lines if x.swap), key=lambda x: -x.swap.saving):
            s = line.swap
            mark = " *" if s.marginal else "  "
            print(f"   {mark}{line.group:12}{line.was[:24]:24}{s.from_price:7.0f}"
                  f" → {line.label[:24]:24}{s.to_price:7.0f} {UNIT[s.unit]:5}"
                  f"{signed(-s.saving):>8} ₽ ({s.gain_share:.0%})")
        if any(x.swap.marginal for x in f.lines if x.swap):
            print(f"   * — выгода меньше {M.MIN_GAIN_SHARE:.0%}; такая замена применена")
            print("       только потому, что иначе группу пришлось бы выбросить")

    print(f"\n  Корзина, позиций: {len(f.lines)}")
    for dept, lines in f.by_dept().items():
        print(f"\n  {dept}")
        for line in lines:
            mark = "*" if line.reason == "обязательная" else ("→" if line.swap else " ")
            print(f"   {mark} {line.group:14}×{line.qty:<3}{money(line.amount):>8} ₽"
                  f"   {line.label[:30]}")

    if f.dropped:
        print("\n  Выброшено, с самого нерегулярного:")
        for line in f.dropped:
            stat = prof.groups.get(line.group)
            rate = f"{stat.per_month:.2f}/мес" if stat else "—"
            print(f"     {line.group:14}{money(line.amount):>8} ₽   берётся {rate}")

    print(f"\n  Покрытие рычага. Строк в корзине {f.considered}; цену в окне "
          f"{catalog.window[0]} … {catalog.window[1]}")
    print(f"  каталог знает у {f.comparable}, заменить было чем {f.substitutable}. "
          f"Почему не у всех:")
    for reason, n in Counter(x.blocked for x in f.lines if x.blocked).most_common():
        print(f"     {n:3}  {reason}")
    if not f.substitution_allowed:
        print("\n  Замены не предлагались вовсе: горизонт подбора — разовая среда,")
        print("  где верить можно только цене (SPEC §8.10).")


def cmd_lapsed(args):
    _history, _settings, prof = load(args)
    items = P.lapsed(prof, asof=args.asof) if args.all else P.to_restock(prof, asof=args.asof)
    asof = args.asof or prof.span[1][:10]
    head(f"8.4 ЧТО ДАВНО НЕ ПОКУПАЛ (на {asof})")
    if not items:
        print("  Всё в срок — просроченных групп нет.")
        return
    print(f"    {'группа':14}{'последняя':>12}{'дней':>6}{'интервал':>10}"
          f"{'просрочка':>11}  что брать")
    for x in items:
        print(f"    {x.group:14}{x.last_ts[:10]:>12}{x.days_since:6}"
              f"{x.median_gap_days:8.0f} дн{x.overdue:10.1f}×  {x.label[:26]}")
    total = sum(x.expected_amount for x in items)
    print(f"\n  Докупить {len(items)} групп, ориентировочно {money(total)} ₽")
    if args.all:
        print("  Показаны и оставленные привычки (--all); без флага — только к докупке.")


def cmd_prices(args):
    history, _settings, prof = load(args)
    kw = {"include_blended": bool(args.blended),
          "include_mode_changed": bool(args.blended)}
    trends = (P.dynamics(history, **kw) if args.all else
              P.prices.for_profile(prof, history, **kw))
    head("8.5 ДИНАМИКА ЦЕН — внутри SKU, приоритет абсолютному рублю")
    if not trends:
        print("  Сравнивать нечего: ни у одного ключа нет двух годов "
              "с тремя наблюдениями.")
        return
    s = P.prices.summary(trends)          # сводка по ВСЕМУ ряду, а не по показанному
    trends = trends[:args.limit] if args.limit else trends
    print(f"  рядов {s['keys']} за {s['span'][0]}–{s['span'][1]}: "
          f"подорожало {s['up']}, подешевело {s['down']}, "
          f"типичное изменение {s['median_pct']:+.0%}\n")
    print(f"    {'товар':32}{'было':>9}{'стало':>9}{'изм.':>9}{'%':>7}  годы")
    for t in trends:
        print(f"    {t.key.label[:32]:32}{t.first.median:9.0f}{t.last.median:9.0f}"
              f"{t.delta_abs:+9.0f}{t.delta_pct:+7.0%}  "
              f"{t.first.year}→{t.last.year} {UNIT[t.unit]}")
    if args.blended:
        print(f"\n  Показаны и смешанные ключи ({s['blended']} из {s['keys']}) —")
        print("  внутри такого ключа лежат разные товары, и «рост» там может")
        print("  оказаться сменой марки, а не подорожанием.")
    else:
        print("\n  Исключены ключи с нераспознанной маркой (внутри такого ключа")
        print("  лежат разные товары) и ряды, где товар стали продавать иначе —")
        print("  на вес вместо штук. Показать их: --blended.")


def cmd_venues(args):
    history, _settings, prof = load(args)
    if args.basket:
        b = P.for_period(prof, args.basket)
        route = P.smart_basket(history, b)
        head(f"8.6 УМНАЯ КОРЗИНА — корзина на {args.basket}")
        if not route.lines:
            print("  Ни одну строку корзины не удалось сравнить между магазинами.")
            return
        print(f"  в одном месте ({route.best_single}): {money(route.baseline_total)} ₽")
        print(f"  врозь по {len(route.venues)} магазинам: {money(route.split_total)} ₽")
        print(f"  экономия {money(route.saving_abs)} ₽ ({route.saving_pct:.0%})\n")
        print(f"    {'товар':30}{'магазин':13}{'цена':>8}{'возраст':>9}"
              f"{'экономия':>10}")
        for line in route.lines:
            mark = " ←" if line.substituted else "  "
            age = f"{line.months_old:.0f} мес" if line.months_old is not None else "—"
            print(f"   {mark}{line.key.label[:29]:29}{line.venue[:13]:13}"
                  f"{line.unit_price:8.0f}{age:>9}{signed(line.saving):>10}")
        print(f"\n  Посчитано по {len(route.lines)} строкам из "
              f"{route.considered} — это {route.covered:.0%} корзины.")
        print(f"  Сравниваются только современники: цены за последние "
              f"{PRICE_WINDOW_MONTHS} месяцев")
        print("  у всех магазинов сразу. Иначе сравнение мерит не разницу")
        print("  магазинов, а разницу лет.")
        if route.substituted:
            print(f"  ← {len(route.substituted)}: типичный товар сравнить не с чем, "
                  f"цена взята по другой")
            print("     вашей регулярной марке той же группы — проверьте, за чем едете:")
            for line in route.substituted:
                print(f"       {line.key.label[:30]:30} вместо "
                      f"{line.instead_of.label[:28]:28} ({line.share_of_typical:.0%})")
        print(f"  Не вошло: {len(route.skipped)} строк сравнить не с чем — цена "
              f"известна не у двух")
        print("  магазинов сразу.", end="")
        if route.outside_baseline:
            print(f" Ещё {len(route.outside_baseline)}: сравнить можно, но у "
                  f"базового", end="")
            print("\n  магазина этого товара нет.", end="")
        print(" В экономию они не засчитаны.")
        return

    head("8.6 ГДЕ ЧТО ДЕШЕВЛЕ — медиана цены по магазинам")
    rows = P.compare(history, limit=args.limit)
    if not rows:
        print("  Сравнивать нечего.")
        return
    print(f"    {'товар':28}{'разрыв':>8}{'%':>7}{'возраст':>9}  дешевле … дороже")
    for c in rows:
        line = " | ".join(f"{p.venue} {p.median:.0f}" for p in c.prices)
        age = f"{c.months_old:.0f} мес" if c.months_old is not None else "—"
        print(f"    {c.key.label[:28]:28}{c.spread_abs:8.0f}{c.spread_pct:+7.0%}"
              f"{age:>9}  {line[:52]}")
    print(f"\n  Сравнимых товаров {len(P.compare(history))}: нужно не меньше трёх")
    print(f"  покупок в каждом из не менее чем двух магазинов (Р-2), и все —")
    print(f"  за последние {PRICE_WINDOW_MONTHS} месяцев: цену 2017 года не с чем")
    print("  сравнивать в 2026-м (П-8).")


def cmd_choose(args):
    history, settings, _prof = load(args)
    head("8.7 ВЫБОР МАГАЗИНА — цена из чеков, качество и обстановка от вас")
    scores = P.rank(history, settings)
    if not scores:
        print("  Данных не хватает: ни у одного магазина нет пяти сравнимых товаров.")
        return
    print(f"    {'магазин':16}{'итог':>7}{'цена':>8}{'индекс':>9}"
          f"{'кач-во':>8}{'обст.':>7}{'товаров':>9}")
    for s in scores:
        q = "—" if s.quality is None else f"{s.quality}★"
        a_ = "—" if s.ambience is None else f"{s.ambience}★"
        print(f"    {s.venue[:16]:16}{s.total:7.2f}{s.price_score:8.2f}"
              f"{s.price_index:9.2f}{q:>8}{a_:>7}{s.keys:9}")
    basis = scores[0].basis
    print(f"\n  Итог сложен из: {', '.join(basis)}.")
    if basis == ("цена",):
        if any(s.rated for s in scores):
            print("  Оценки есть не у всех магазинов, поэтому в итог они не вошли:")
            print("  иначе пять звёзд ОПУСКАЛИ бы оценённый магазин ниже")
            print("  неоценённого, у которого в итоге осталась бы одна цена.")
        else:
            print("  Оценок качества и обстановки нет. Этих данных в чеках не")
            print("  бывает: их ставит человек (SPEC §8.7).")
    print("  Индекс 0,90 значит «в среднем на 10% дешевле остальных».")


def cmd_spending(args):
    history, _settings, _prof = load(args)
    summary = P.summary(history)
    if args.growth:
        head(f"8.8 ГДЕ ТРАТЫ РАСТУТ — разрез «{args.by}», "
             f"{args.months} мес против предыдущих {args.months}")
        trends = P.growth(history, args.by, months=args.months, limit=args.limit)
        print(f"    {'ключ':26}{'было':>11}{'стало':>11}{'изменение':>12}{'%':>9}")
        for t in trends:
            pct = "—" if t.delta_pct == float("inf") else f"{t.delta_pct:+.0%}"
            print(f"    {str(t.key)[:26]:26}{money(t.before):>11}{money(t.after):>11}"
                  f"{signed(t.delta_abs):>12}{pct:>9}")
        print("\n  Ранжировано по абсолютному рублю: процент вспомогателен (§8.5).")
        return

    head(f"8.8 КОНТРОЛЬ ТРАТ — разрез «{args.by}»")
    print(f"  всего {money(summary['total'])} ₽ за {summary['months']} мес "
          f"({summary['first_month']} … {summary['last_month']}), "
          f"типичный месяц {money(summary['median_month'])} ₽\n")
    buckets = P.breakdown(history, args.by, limit=args.limit)
    order = sorted(buckets, key=lambda b: b.key) if args.by in ("year", "month") else buckets
    print(f"    {'ключ':26}{'сумма':>12}{'сделок':>9}{'доля':>8}")
    for b in order:
        print(f"    {str(b.key)[:26]:26}{money(b.amount):>12}{b.txns:9}{b.share:8.1%}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", help="путь к чекам; по умолчанию data/receipts.json")
    parser.add_argument("--candidates", action="store_true",
                        help="включить ярус food_candidate (SPEC §7)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("profile", help="8.1 профиль предпочтений").set_defaults(fn=cmd_profile)

    p = sub.add_parser("basket", help="8.2 корзина «как обычно»")
    p.add_argument("--period", choices=("day", "week", "month"))
    p.add_argument("--budget", type=float)
    p.set_defaults(fn=cmd_basket)

    p = sub.add_parser("budget", help="8.3 уложиться в сумму")
    p.add_argument("amount", type=float, help="сколько ₽ можно потратить")
    p.add_argument("--period", default="week", choices=("day", "week", "month"),
                   help="на какой срок корзина; по умолчанию неделя")
    p.set_defaults(fn=cmd_budget)

    p = sub.add_parser("lapsed", help="8.4 что давно не покупал")
    p.add_argument("--asof", help="дата отсчёта; по умолчанию конец истории")
    p.add_argument("--all", action="store_true", help="включая оставленные привычки")
    p.set_defaults(fn=cmd_lapsed)

    p = sub.add_parser("prices", help="8.5 динамика цен")
    p.add_argument("--all", action="store_true",
                   help="по всем товарам, а не только по постоянной корзине")
    p.add_argument("--blended", action="store_true",
                   help="включая ключи с нераспознанной маркой")
    p.add_argument("--limit", type=int, default=20, help="сколько строк показать")
    p.set_defaults(fn=cmd_prices)

    p = sub.add_parser("venues", help="8.6 сравнение магазинов")
    p.add_argument("--basket", choices=("day", "week", "month"),
                   help="развести корзину по магазинам и показать экономию")
    p.add_argument("--limit", type=int, default=20, help="сколько строк показать")
    p.set_defaults(fn=cmd_venues)

    sub.add_parser("choose", help="8.7 выбор магазина").set_defaults(fn=cmd_choose)

    p = sub.add_parser("spending", help="8.8 контроль трат")
    p.add_argument("--by", default="year",
                   choices=("year", "month", "venue", "dept", "group", "segment"))
    p.add_argument("--growth", action="store_true", help="где траты растут")
    p.add_argument("--months", type=int, default=12, help="ширина окна сравнения")
    p.add_argument("--limit", type=int, help="сколько строк показать")
    p.set_defaults(fn=cmd_spending)

    args = parser.parse_args(argv)
    args.fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
