"""8.2 Корзина «как обычно» (SPEC §8.2).

Уровень атома — группа: пользователь думает «нужна сметана». Но показывать
одну группу мало, поэтому под неё подставляется типичный ключ — самый частый
внутри группы (SPEC §4, таблица: «берётся группа, подставляется самый частый
SKU»).

Два режима, оба из §8.2:
  под период      — что обычно берётся за неделю или месяц;
  под средний чек — что обычно набирается за один поход.
"""

from dataclasses import dataclass
from datetime import date

#: длительность периодов в месяцах
PERIODS = {"week": 7 / 30.44, "month": 1.0, "day": 1 / 30.44}
#: реже, чем раз в два периода, — в корзину периода не попадает
MIN_EXPECTED = 0.5


@dataclass(frozen=True)
class BasketLine:
    group: str
    dept: str
    times: float          # сколько раз за период обычно берётся
    qty: int              # сколько штук класть в корзину
    amount: float         # ожидаемая сумма
    typical_key: object   # чем именно эта группа обычно закрывается
    unit_price: float     # ₽ за базовую единицу типичного ключа
    reason: str           # «регулярная» или «обязательная»

    @property
    def label(self):
        return self.typical_key.label if self.typical_key else "—"


@dataclass(frozen=True)
class Basket:
    mode: str
    period: str
    lines: tuple
    total: float

    def __len__(self):
        return len(self.lines)

    def by_dept(self):
        out = {}
        for line in self.lines:
            out.setdefault(line.dept or "—", []).append(line)
        return out


def _line(stat, times, required):
    qty = max(1, round(times))
    return BasketLine(
        group=stat.group,
        dept=stat.dept,
        times=times,
        qty=qty,
        amount=stat.median_amount * qty,
        typical_key=stat.typical_key,
        unit_price=stat.typical_unit_price,
        reason="обязательная" if required else "регулярная",
    )


def for_period(profile, period="week", settings=None):
    """Что обычно берётся за неделю или месяц.

    Обязательные позиции пользователя попадают всегда, даже если по частоте
    не проходят: он сказал, что они нужны (SPEC §3.3).
    """
    if period not in PERIODS:
        raise ValueError(f"период {period!r}: ожидался один из {sorted(PERIODS)}")
    required = set(getattr(settings, "required_groups", ()) or ())
    months = PERIODS[period]

    lines = []
    for group in profile.staples:
        stat = profile.groups.get(group)
        if stat is None:
            continue
        times = stat.per_month * months
        if times < MIN_EXPECTED and group not in required:
            continue
        lines.append(_line(stat, times, group in required))

    lines.sort(key=lambda x: -x.amount)
    return Basket(mode="period", period=period, lines=tuple(lines),
                  total=sum(x.amount for x in lines))


def for_average_txn(profile, budget=None, settings=None):
    """Что обычно набирается за один поход — по среднему чеку.

    Группы добавляются по убыванию частоты, пока не наберётся средний чек.
    Это не оптимизация под бюджет: замена дорогого на дешёвое — рычаг §8.3
    и появится в С4. Здесь только «как обычно».
    """
    required = set(getattr(settings, "required_groups", ()) or ())
    target = budget or profile.avg_txn

    lines, total = [], 0.0
    for stat in profile.ranked():
        if stat.group not in profile.staples and stat.group not in required:
            continue
        if total + stat.median_amount > target and stat.group not in required:
            continue
        line = _line(stat, 1.0, stat.group in required)
        lines.append(line)
        total += line.amount

    return Basket(mode="average_txn", period=None, lines=tuple(lines),
                  total=total)
