#!/usr/bin/env python3
"""Командная строка агента-закупщика — функции ядра А на уровне группы.

    python3 cli.py profile                     8.1 профиль предпочтений
    python3 cli.py basket --period week        8.2 корзина «как обычно»
    python3 cli.py basket --budget 2000        8.2 то же под заданную сумму
    python3 cli.py lapsed                      8.4 что давно не покупал
    python3 cli.py spending --by dept          8.8 контроль трат
    python3 cli.py spending --growth --by group   где траты растут

Это временная оболочка: интерфейс — веб и бот, они в С5 и С6 (DECISIONS Р-1).
Числа здесь считает ядро, CLI только печатает.
"""

import argparse
import sys
from functools import lru_cache

from agent.config import Config, dataset_path
from agent.settings import Settings
from agent.adapters.receipts_fns import Pipeline, load_receipts
from agent.adapters.receipts_fns.pipeline import to_history
from agent import profile as P

WIDTH = 64


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
    receipts = load_receipts(data or dataset_path())
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

    p = sub.add_parser("lapsed", help="8.4 что давно не покупал")
    p.add_argument("--asof", help="дата отсчёта; по умолчанию конец истории")
    p.add_argument("--all", action="store_true", help="включая оставленные привычки")
    p.set_defaults(fn=cmd_lapsed)

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
