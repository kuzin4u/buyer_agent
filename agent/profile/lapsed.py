"""8.4 Что давно не покупал (SPEC §8.4).

Сравнение частоты покупки группы с датой последнего появления: если группа
берётся часто, но давно не появлялась — предложить докупить.

Уровень атома — группа: «давно не брал сметану», а не «давно не брал вот эту
конкретную банку» (SPEC §4).

Две вещи, которые легко перепутать и которые здесь разведены:

  просрочено   — берётся регулярно, но в этот раз задержалось;
  перестали    — не появлялось столько времени, что это уже не задержка,
                 а смена привычки. Предлагать «докупить» тут неуместно.
"""

from dataclasses import dataclass
from datetime import date

#: во сколько раз просрочка должна превысить обычный интервал, чтобы о ней
#: стоило говорить; полтора интервала — это ещё не тревога, но уже заметно
MIN_OVERDUE = 1.5
#: во сколько раз просрочка должна превысить интервал, чтобы считать привычку
#: оставленной, а не задержанной
ABANDONED_OVERDUE = 10.0

OVERDUE = "докупить"
ABANDONED = "похоже, перестали брать"


@dataclass(frozen=True)
class Lapse:
    group: str
    dept: str
    last_ts: str
    days_since: int
    median_gap_days: float
    overdue: float          # во сколько раз превышен обычный интервал
    verdict: str
    typical_key: object
    expected_amount: float

    @property
    def label(self):
        return self.typical_key.label if self.typical_key else "—"


def lapsed(profile, asof=None, min_overdue=MIN_OVERDUE, only_staples=True):
    """→ список просроченного, самое просроченное первым.

    asof по умолчанию — последняя дата, которая вообще есть в истории. Для
    статичного датасета это честнее сегодняшнего числа: иначе всё разом
    окажется просроченным на столько, сколько датасет пролежал.
    """
    if asof is None:
        asof = profile.span[1][:10] if profile.span[1] else None
    if asof is None:
        return []
    today = date.fromisoformat(str(asof)[:10])

    out = []
    for stat in profile.groups.values():
        if only_staples and stat.group not in profile.staples:
            continue
        if not stat.median_gap_days:
            continue
        days = (today - date.fromisoformat(stat.last_ts[:10])).days
        overdue = days / stat.median_gap_days
        if overdue < min_overdue:
            continue
        out.append(Lapse(
            group=stat.group,
            dept=stat.dept,
            last_ts=stat.last_ts,
            days_since=days,
            median_gap_days=stat.median_gap_days,
            overdue=overdue,
            verdict=ABANDONED if overdue >= ABANDONED_OVERDUE else OVERDUE,
            typical_key=stat.typical_key,
            expected_amount=stat.median_amount,
        ))

    out.sort(key=lambda x: -x.overdue)
    return out


def to_restock(profile, asof=None, min_overdue=MIN_OVERDUE):
    """Только то, что действительно стоит докупить, без оставленных привычек."""
    return [x for x in lapsed(profile, asof, min_overdue) if x.verdict == OVERDUE]
