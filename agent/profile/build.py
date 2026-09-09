"""8.1 Профиль предпочтений — строится автоматически из истории.

Вход — только History и Settings. Слов «чек», «SKU» и «магазин» здесь нет:
это шов §8.10, и он держится не обещанием, а сигнатурой.

Уровень атома (SPEC §4): состав корзины — группы, потому что пользователь
думает «нужна сметана», а не «нужна сметана Viola 23% 315 г». Типичный ключ
внутри группы считается тоже — он понадобится, когда группу нужно будет
превратить в конкретную позицию (§8.2).
"""

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

#: Группа постоянна, если берётся не реже раза в месяц. Критерий частотный, а
#: не долевой, по двум причинам. Он интерпретируем: «беру хотя бы раз в месяц»
#: — это про привычку, а «попадает в 5% чеков» ни о чём не говорит. И он
#: совпадает с естественным обрывом в данных: до 1,00/мес идёт плотный ряд из
#: 28 групп, дальше сразу 0,73.
#: Широкий список — это нормально: постоянная корзина здесь весь регулярный
#: репертуар, а под неделю или месяц его сужает §8.2.
STAPLE_PER_MONTH = 1.0
#: меньше этого числа появлений — не привычка, а случайность
STAPLE_MIN_TXNS = 3
#: Сегменты, которые считаются покупками «за продуктами». Ярус food_candidate
#: сюда НЕ входит: SPEC §7 держит его выключенным до подтверждения пользователем
#: (иначе, например, «Агроаспект» сольётся с уже имеющимся Перекрёстком).
FOOD_SEGMENTS = ("food_core", "unknown_food")
CANDIDATE_SEGMENT = "food_candidate"


def _day(ts):
    return date.fromisoformat(ts[:10])


def _months_between(start, end):
    return max((_day(end) - _day(start)).days / 30.44, 1.0)


@dataclass(frozen=True)
class GroupStat:
    """Что известно про одну единицу потребности."""

    group: str
    txns: int               # в скольких сделках встречалась
    purchases: int          # сколько раз бралась
    amount: float           # сколько на неё потрачено
    first_ts: str
    last_ts: str
    per_month: float        # частота: сколько раз в месяц берётся
    median_gap_days: float  # типичный интервал; None, если покупка была одна
    typical_key: object     # самый частый ключ внутри группы
    share: float            # доля в тратах
    median_amount: float    # типичная сумма на группу в одной сделке
    typical_unit_price: float  # медиана цены типичного ключа, ₽ за базовую единицу
    dept: str = None


@dataclass(frozen=True)
class VenueStat:
    venue: str
    txns: int
    amount: float
    share: float


@dataclass
class Profile:
    """Портрет покупателя. Всё остальное в С2 строится поверх него."""

    principal: str
    span: tuple
    months: float
    txns: int
    spend: float
    avg_txn: float
    groups: dict = field(default_factory=dict)
    staples: tuple = ()
    venues: dict = field(default_factory=dict)
    main_venue: str = None
    main_venue_share: float = 0.0
    dept_shares: dict = field(default_factory=dict)
    segment_shares: dict = field(default_factory=dict)

    def ranked(self):
        """Группы по числу сделок — то, из чего собирается постоянная корзина."""
        return sorted(self.groups.values(), key=lambda g: (-g.txns, g.group))

    def staple_stats(self):
        return [self.groups[g] for g in self.staples if g in self.groups]


def build(history, settings=None, segments=None):
    """История → профиль. Пустая история даёт пустой профиль, а не исключение."""
    from ..settings import Settings
    settings = settings or Settings()
    if segments is None:
        segments = FOOD_SEGMENTS + (
            (CANDIDATE_SEGMENT,) if settings.include_candidates else ())

    food = [o for o in history.outlays if o.segment in segments]
    start, end = history.span()
    months = _months_between(start, end) if start else 1.0

    txn_ids = {o.txn for o in food}
    spend = sum(o.amount for o in food)

    # --- группы ---
    by_group_txns = defaultdict(set)
    by_group_days = defaultdict(set)
    amounts = defaultdict(float)
    per_txn = defaultdict(lambda: defaultdict(float))
    purchases = Counter()
    depts = {}
    for o in food:
        if not o.group:
            continue
        by_group_txns[o.group].add(o.txn)
        by_group_days[o.group].add(o.ts[:10])
        amounts[o.group] += o.amount
        per_txn[o.group][o.txn] += o.amount
        purchases[o.group] += 1
        if o.dept:
            depts[o.group] = o.dept

    keys = defaultdict(Counter)
    prices = defaultdict(list)
    for e in history.events:
        keys[e.key.group][e.key] += 1
        prices[e.key].append(e.unit_price)

    groups = {}
    for group, txns in by_group_txns.items():
        days = sorted(by_group_days[group])
        gaps = [(_day(b) - _day(a)).days for a, b in zip(days, days[1:])]
        typical = keys[group].most_common(1)
        typical_key = typical[0][0] if typical else None
        key_prices = prices.get(typical_key) or []
        groups[group] = GroupStat(
            group=group,
            txns=len(txns),
            purchases=purchases[group],
            amount=amounts[group],
            first_ts=days[0],
            last_ts=days[-1],
            per_month=len(txns) / months,
            median_gap_days=statistics.median(gaps) if gaps else None,
            typical_key=typical_key,
            share=amounts[group] / spend if spend else 0.0,
            median_amount=statistics.median(per_txn[group].values()),
            typical_unit_price=statistics.median(key_prices) if key_prices else None,
            dept=depts.get(group),
        )

    # --- постоянная корзина ---
    staples = [g for g, s in groups.items()
               if s.per_month >= STAPLE_PER_MONTH and s.txns >= STAPLE_MIN_TXNS]
    staples += [g for g in settings.required_groups if g not in staples]
    staples = [g for g in staples if g not in settings.excluded_groups]
    staples.sort(key=lambda g: -groups[g].txns if g in groups else 0)

    # --- площадки ---
    # Покупки с неустановленной площадкой (venue is None) в рейтинг не идут:
    # доля магазина считается от того, что вообще привязано к магазину.
    venue_txns = defaultdict(set)
    venue_amount = defaultdict(float)
    for o in food:
        if o.venue is None:
            continue
        venue_txns[o.venue].add(o.txn)
        venue_amount[o.venue] += o.amount
    identified = sum(venue_amount.values())
    venues = {v: VenueStat(venue=v, txns=len(t), amount=venue_amount[v],
                           share=venue_amount[v] / identified if identified else 0.0)
              for v, t in venue_txns.items()}
    main = max(venues.values(), key=lambda v: v.amount, default=None)
    if settings.main_venue and settings.main_venue in venues:
        main = venues[settings.main_venue]

    # --- доли трат ---
    dept_amount = defaultdict(float)
    for o in food:
        if o.dept:
            dept_amount[o.dept] += o.amount
    seg_amount = defaultdict(float)
    for o in history.outlays:
        seg_amount[o.segment] += o.amount
    all_spend = sum(seg_amount.values()) or 1

    return Profile(
        principal=history.principal,
        span=(start, end),
        months=months,
        txns=len(txn_ids),
        spend=spend,
        avg_txn=spend / len(txn_ids) if txn_ids else 0.0,
        groups=groups,
        staples=tuple(staples),
        venues=venues,
        main_venue=main.venue if main else None,
        main_venue_share=main.share if main else 0.0,
        dept_shares={k: v / spend for k, v in dept_amount.items()} if spend else {},
        segment_shares={k: v / all_spend for k, v in seg_amount.items()},
    )
