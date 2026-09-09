"""8.8 Контроль трат (SPEC §8.8).

По годам, месяцам, площадкам, отделам и группам. Считается по ВСЕМ тратам,
включая непродуктовое: «это тоже деньги пользователя». Поэтому вход — поток
Outlay, а не Event: Event знает только то, у чего есть сопоставимая цена, и
2 млн ₽ из 5,6 в него не попадают.

Выявление роста устроено по правилу §8.5: приоритетная мера — абсолютный рост
в рублях, процент вспомогательный. Группа, подорожавшая на 300%, но с 200 до
800 ₽ в год, — это не то, ради чего стоит менять привычки.
"""

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

UNSET = "не определено"


def _unclassified(outlay):
    """Почему у траты нет группы. Свалить всё в одно «не определено» значит
    спрятать 1,7 млн ₽ разной природы: аптека и нераспознанный творог — это
    не одно и то же."""
    if outlay.segment == "non_food":
        return "непродуктовое"
    if outlay.segment == "unknown":
        return "контрагент не установлен"
    return "не отнесено к группе"


#: как разложить трату по выбранному разрезу
DIMENSIONS = {
    "year": lambda o: o.year,
    "month": lambda o: o.month,
    "venue": lambda o: o.venue or UNSET,
    "dept": lambda o: o.dept or _unclassified(o),
    "group": lambda o: o.group or _unclassified(o),
    "segment": lambda o: o.segment or UNSET,
}


@dataclass(frozen=True)
class Bucket:
    key: str
    amount: float
    txns: int
    share: float


@dataclass(frozen=True)
class Trend:
    """Как изменились траты в одном разрезе между двумя окнами."""

    key: str
    before: float
    after: float
    delta_abs: float
    delta_pct: float

    @property
    def direction(self):
        if self.delta_abs > 0:
            return "рост"
        return "снижение" if self.delta_abs < 0 else "без изменений"


def _select(history, segments=None):
    if segments is None:
        return history.outlays
    return [o for o in history.outlays if o.segment in segments]


def breakdown(history, dimension="year", segments=None, limit=None):
    """→ разрез трат, самое дорогое первым."""
    if dimension not in DIMENSIONS:
        raise ValueError(
            f"разрез {dimension!r}: ожидался один из {sorted(DIMENSIONS)}")
    pick = DIMENSIONS[dimension]

    amounts = defaultdict(float)
    txns = defaultdict(set)
    for o in _select(history, segments):
        key = pick(o)
        amounts[key] += o.amount
        txns[key].add(o.txn)

    total = sum(amounts.values()) or 1.0
    out = [Bucket(key=k, amount=v, txns=len(txns[k]), share=v / total)
           for k, v in amounts.items()]
    out.sort(key=lambda b: -b.amount)
    return out[:limit] if limit else out


def _window(outlays, end, months):
    """Траты за `months` месяцев, заканчивающихся датой end включительно."""
    start = date.fromisoformat(end) - _months(months)
    return [o for o in outlays
            if start < date.fromisoformat(o.ts[:10]) <= date.fromisoformat(end)]


class _months:
    """Сдвиг на N месяцев назад, без внешних зависимостей."""

    def __init__(self, n):
        self.n = n

    def __rsub__(self, d):
        month = d.month - self.n
        year = d.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 or not year % 400)
                          else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
        return date(year, month, day)


def growth(history, dimension="group", months=12, asof=None, segments=None,
           limit=None):
    """Где траты растут: последние N месяцев против тех же N годом раньше.

    Сравнение окно-в-окно, а не «год к году», намеренно. Последний год в
    истории почти всегда неполный, и календарное сравнение показало бы
    обвал там, где просто ещё не наступил декабрь.
    """
    if dimension not in DIMENSIONS:
        raise ValueError(
            f"разрез {dimension!r}: ожидался один из {sorted(DIMENSIONS)}")
    pick = DIMENSIONS[dimension]

    outlays = _select(history, segments)
    if not outlays:
        return []
    end = (asof or max(o.ts for o in outlays))[:10]

    now = _window(outlays, end, months)
    then_end = (date.fromisoformat(end) - _months(months)).isoformat()
    then = _window(outlays, then_end, months)

    after = defaultdict(float)
    before = defaultdict(float)
    for o in now:
        after[pick(o)] += o.amount
    for o in then:
        before[pick(o)] += o.amount

    out = []
    for key in set(after) | set(before):
        a, b = after.get(key, 0.0), before.get(key, 0.0)
        out.append(Trend(
            key=key, before=b, after=a, delta_abs=a - b,
            delta_pct=((a - b) / b) if b else float("inf") if a else 0.0))

    # Приоритетная мера — абсолютный рост в рублях (SPEC §8.5)
    out.sort(key=lambda t: -abs(t.delta_abs))
    return out[:limit] if limit else out


def monthly_series(history, segments=None):
    """Помесячный ряд трат — основа графика в вебе (С5)."""
    amounts = defaultdict(float)
    for o in _select(history, segments):
        amounts[o.month] += o.amount
    return [(m, amounts[m]) for m in sorted(amounts)]


def summary(history, segments=None):
    """Короткая сводка: сколько всего, за какой срок, каков типичный месяц."""
    series = monthly_series(history, segments)
    total = sum(a for _m, a in series)
    return {
        "total": total,
        "months": len(series),
        "median_month": statistics.median([a for _m, a in series]) if series else 0.0,
        "first_month": series[0][0] if series else None,
        "last_month": series[-1][0] if series else None,
    }
