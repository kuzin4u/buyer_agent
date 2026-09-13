"""8.5 Динамика цен (SPEC §8.5).

Рост цены считается ТОЛЬКО внутри SKU. Это не оговорка, а суть: средняя цена
по группе меняется, когда человек переходит с одной марки на другую, и такой
«рост» говорит о смене привычки, а не о подорожании. Внутри ключа сравнивается
одно и то же — марка, жирность, фасовка совпадают по построению (SPEC §4).

Приоритетная мера — абсолютный рост в рублях за базовую единицу, процент
вспомогательный. Позиция, подорожавшая на 300% с 20 до 80 ₽/кг, стоит внимания
меньше, чем подорожавшая на 15% с 900 до 1035 ₽/кг, — а сортировка по проценту
показала бы обратное. То же правило уже принято для трат (Р-11, spending.py).

Единица у каждого ряда своя — ₽/кг, ₽/л или ₽/шт, она берётся из ключа. Числа
из разных рядов не складываются: смешать ₽/кг с ₽/шт значит получить
бессмысленную сумму.
"""

import statistics
from collections import defaultdict
from dataclasses import dataclass


#: Меньше трёх наблюдений в году — не медиана, а шум. Порог тот же, что у
#: сопоставимости SKU, и снижать его нельзя (docs/DECISIONS.md Р-2).
MIN_OBSERVATIONS = 3
#: Ряд короче двух годов сравнивать не с чем.
MIN_YEARS = 2


@dataclass(frozen=True)
class YearPrice:
    """Медианная цена одного ключа за один год."""

    year: str
    median: float
    observations: int


@dataclass(frozen=True)
class PriceTrend:
    """Как изменилась цена внутри одного ключа между крайними годами ряда."""

    key: object
    unit: str
    first: YearPrice
    last: YearPrice
    delta_abs: float          # ₽ за базовую единицу
    delta_pct: float          # доля, не проценты: 0.15 — это +15%
    series: tuple             # весь ряд YearPrice, по возрастанию года
    blended: bool = False     # марка внутри ключа неизвестна, см. is_blended
    mode_changed: bool = False  # товар стали продавать иначе, см. _mode_changed

    @property
    def direction(self):
        if self.delta_abs > 0:
            return "рост"
        return "снижение" if self.delta_abs < 0 else "без изменений"

    @property
    def years(self):
        return int(self.last.year) - int(self.first.year)

    @property
    def per_year_abs(self):
        """Среднегодовой рост в рублях: ряды разной длины иначе не сравнить."""
        return self.delta_abs / self.years if self.years else 0.0


def is_blended(key):
    """Ключ, внутри которого лежит не один товар (history.ItemKey.blended)."""
    return key.blended


def _series_from(by_year, min_observations):
    """Ряд из уже разложенных по годам покупок."""
    return tuple(
        YearPrice(year=y, median=statistics.median([e.unit_price for e in evts]),
                  observations=len(evts))
        for y, evts in sorted(by_year.items()) if len(evts) >= min_observations)


def series(history, key, min_observations=MIN_OBSERVATIONS):
    """→ ряд YearPrice по одному ключу, годы по возрастанию.

    Год, в котором покупок меньше порога, в ряд не попадает: одна покупка по
    акции сделала бы «падение цены на 40%».
    """
    by_year = defaultdict(list)
    for e in history.events:
        if e.key == key and e.unit_price is not None:
            by_year[e.year].append(e)
    return _series_from(by_year, min_observations)


def _fractional_share(events):
    """Доля покупок с дробным количеством. Дробное — значит на вес."""
    if not events:
        return None
    return sum(1 for e in events
               if abs(e.qty - round(e.qty)) > 1e-9) / len(events)


def _mode_changed(first_year_events, last_year_events):
    """Товар начали продавать иначе: был на вес, стал поштучно или наоборот.

    Тогда ряд сравнивает ₽/кг с ₽/шт под одним именем. В выборке такой ряд
    один — «КОТЛЕТЫ ДОМАШНИЕ ЖАР»: в 2017 их брали по 0,94 кг за 369 ₽/кг, в
    2024 — по одной штуке за 77 ₽. «Подешевело на 79%» здесь неправда, и
    неправда убедительная: и цена, и название настоящие.

    Признак тот же, по которому весовой товар вообще опознаётся (доля дробных
    количеств, normalization.json → weighted_goods), только считается он по
    крайним годам ряда отдельно, а не по всей истории названия сразу.
    """
    first = _fractional_share(first_year_events)
    last = _fractional_share(last_year_events)
    if first is None or last is None:
        return False
    return (first >= 0.5) != (last >= 0.5)


def dynamics(history, groups=None, min_observations=MIN_OBSERVATIONS,
             min_years=MIN_YEARS, limit=None, include_blended=False,
             include_mode_changed=False):
    """→ динамика по всем ключам, где ряд достаточно длинный.

    Сортировка по модулю абсолютного изменения: наверху то, что сильнее всего
    двигает расходы, независимо от того, подорожало оно или подешевело.

    Из ответа по умолчанию убраны две породы рядов, где число выглядит как
    динамика цены, но ею не является: ключи с нераспознанной маркой (83 из 206
    на текущем датасете, см. is_blended) и ряды, где товар стали продавать
    иначе (1 из 206, см. _mode_changed). Оба включаются флагами — не спрятаны,
    а отделены.
    """
    # Один проход по истории: раскладка по ключам и годам считается заранее.
    # Иначе на каждый ключ шёл бы отдельный проход — 200 ключей × 19 тысяч
    # покупок, и прогон тестов вырастал до минуты.
    by_key_year = defaultdict(lambda: defaultdict(list))
    for e in history.events:
        if e.key is None or e.unit_price is None:
            continue
        if groups and e.key.group not in groups:
            continue
        by_key_year[e.key][e.year].append(e)

    out = []
    for key, by_year in by_key_year.items():
        blended = is_blended(key)
        if blended and not include_blended:
            continue
        row = _series_from(by_year, min_observations)
        if len(row) < min_years:
            continue
        first, last = row[0], row[-1]
        changed = _mode_changed(by_year[first.year], by_year[last.year])
        if changed and not include_mode_changed:
            continue
        delta = last.median - first.median
        out.append(PriceTrend(
            key=key, unit=key.unit, first=first, last=last,
            delta_abs=delta,
            delta_pct=delta / first.median if first.median else 0.0,
            series=row, blended=blended, mode_changed=changed))
    out.sort(key=lambda t: (-abs(t.delta_abs), t.key.group, t.key.label))
    return out[:limit] if limit else out


def for_profile(profile, history, staples_only=True, **kwargs):
    """Динамика по тому, что человек берёт постоянно.

    Полный список бесполезен как ответ на вопрос «что подорожало»: в нём
    окажется и то, что куплено трижды за девять лет.
    """
    groups = set(profile.staples) if staples_only else None
    return dynamics(history, groups=groups, **kwargs)


def summary(trends):
    """Сводка по набору рядов: сколько подорожало, сколько подешевело.

    Суммарный рост в рублях НЕ считается намеренно: у рядов разные единицы, и
    сложение ₽/кг с ₽/шт дало бы правдоподобное, но бессмысленное число.
    """
    up = [t for t in trends if t.delta_abs > 0]
    down = [t for t in trends if t.delta_abs < 0]
    return {
        "keys": len(trends),
        "up": len(up),
        "down": len(down),
        "flat": len(trends) - len(up) - len(down),
        "blended": sum(1 for t in trends if t.blended),
        "mode_changed": sum(1 for t in trends if t.mode_changed),
        "median_pct": (statistics.median([t.delta_pct for t in trends])
                       if trends else 0.0),
        "span": ((min(t.first.year for t in trends),
                  max(t.last.year for t in trends)) if trends else (None, None)),
    }
