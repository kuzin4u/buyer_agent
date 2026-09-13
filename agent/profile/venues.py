"""8.6 Сравнение площадок и 8.7 выбор площадки (SPEC §8.6, §8.7).

Обе функции стоят на одном основании — медианной цене одного ключа у одной
площадки, — поэтому живут в одном модуле: разведи их, и порог сопоставимости
разъедется на два.

Сравнивать можно только внутри ключа и только там, где наблюдений хватает на
медиану: не меньше трёх у каждой из не менее чем двух площадок. Это тот же
порог, что у диагностики покрытия, и снижать его нельзя (Р-2). Цена такого
правила видна сразу: сравнимых ключей всегда сильно меньше, чем купленных, и
«умная корзина» честно говорит, какую долю корзины она вообще может развести
по площадкам.
"""

import statistics
from collections import defaultdict
from dataclasses import dataclass

#: наблюдений у одной площадки, чтобы её медиана что-то значила (Р-2)
MIN_OBSERVATIONS = 3
#: площадок, чтобы было что с чем сравнивать
MIN_VENUES = 2
#: сравнимых ключей, чтобы у площадки был ценовой ранг, а не одно совпадение
MIN_KEYS_FOR_RANK = 5


@dataclass(frozen=True)
class VenuePrice:
    venue: str
    median: float
    observations: int


@dataclass(frozen=True)
class KeyComparison:
    """Цена одного ключа у всех площадок, где её можно померить."""

    key: object
    unit: str
    prices: tuple             # VenuePrice, от дешёвой к дорогой

    @property
    def cheapest(self):
        return self.prices[0]

    @property
    def dearest(self):
        return self.prices[-1]

    @property
    def spread_abs(self):
        return self.dearest.median - self.cheapest.median

    @property
    def spread_pct(self):
        return self.spread_abs / self.cheapest.median if self.cheapest.median else 0.0


def _by_key_venue(history, keys=None, groups=None):
    prices = defaultdict(lambda: defaultdict(list))
    for e in history.events:
        if e.key is None or e.unit_price is None or not e.venue:
            continue
        if keys is not None and e.key not in keys:
            continue
        if groups and e.key.group not in groups:
            continue
        prices[e.key][e.venue].append(e.unit_price)
    return prices


def compare(history, keys=None, groups=None, min_observations=MIN_OBSERVATIONS,
            min_venues=MIN_VENUES, limit=None, include_blended=False):
    """→ ключи, цену которых можно сравнить между площадками.

    Сортировка по абсолютному разрыву: наверху то, где выбор площадки стоит
    дороже всего. Процент показывается рядом, но не сортирует, — правило то
    же, что в §8.5.

    Ключи с нераспознанной маркой (ItemKey.blended) по умолчанию исключены.
    Без отсева верх списка занимает «заморозка 0,08 кг: 531 ₽/кг у одних,
    2 000 ₽/кг у других» — это разные товары, а не разные цены, и вести
    человека в магазин по такому расчёту нельзя.
    """
    out = []
    for key, by_venue in _by_key_venue(history, keys, groups).items():
        if key.blended and not include_blended:
            continue
        rows = [VenuePrice(venue=v, median=statistics.median(p), observations=len(p))
                for v, p in by_venue.items() if len(p) >= min_observations]
        if len(rows) < min_venues:
            continue
        rows.sort(key=lambda r: (r.median, r.venue))
        out.append(KeyComparison(key=key, unit=key.unit, prices=tuple(rows)))
    out.sort(key=lambda c: (-c.spread_abs, c.key.group, c.key.label))
    return out[:limit] if limit else out


@dataclass(frozen=True)
class RouteLine:
    """Одна строка корзины, разведённая по площадкам."""

    key: object
    unit: str
    units: float              # сколько базовых единиц берётся
    venue: str                # где дешевле
    unit_price: float
    cost: float
    baseline_cost: float      # та же строка у лучшей одиночной площадки

    @property
    def saving(self):
        return self.baseline_cost - self.cost


@dataclass(frozen=True)
class Route:
    """Маршрут экономии против покупки всего в одном месте."""

    lines: tuple
    skipped: tuple            # строки корзины, которые сравнить не удалось
    best_single: str
    single_totals: dict       # {площадка: сумма сравнимой части корзины}
    split_total: float
    baseline_total: float

    @property
    def saving_abs(self):
        return self.baseline_total - self.split_total

    @property
    def saving_pct(self):
        return self.saving_abs / self.baseline_total if self.baseline_total else 0.0

    @property
    def venues(self):
        return sorted({line.venue for line in self.lines})

    @property
    def covered(self):
        """Доля корзины, которую удалось развести: без неё экономия — половина
        правды. Пять процентов корзины, разложенные идеально, это не экономия."""
        total = len(self.lines) + len(self.skipped)
        return len(self.lines) / total if total else 0.0


def _units(line):
    """Сколько базовых единиц стоит за строкой корзины."""
    if not line.unit_price:
        return None
    return line.amount / line.unit_price


def smart_basket(history, basket, min_observations=MIN_OBSERVATIONS,
                 min_venues=MIN_VENUES):
    """Корзина, разведённая по площадкам, против лучшей одиночной площадки.

    База сравнения — не «текущие траты», а лучшая ОДИНОЧНАЯ площадка: вопрос
    пользователя звучит «стоит ли ехать в два места вместо одного», и честный
    ответ сравнивает именно эти два сценария.

    Строки, которые сравнить не удалось, в экономию не засчитываются и уходят
    в skipped. Иначе достаточно было бы не найти цену у дорогой площадки,
    чтобы «сэкономить».
    """
    comparisons = {c.key: c for c in compare(
        history, keys={l.typical_key for l in basket.lines if l.typical_key},
        min_observations=min_observations, min_venues=min_venues)}

    priced, skipped = [], []
    for line in basket.lines:
        units = _units(line)
        if line.typical_key not in comparisons or units is None:
            skipped.append(line)
        else:
            priced.append((line, units, comparisons[line.typical_key]))

    if not priced:
        return Route(lines=(), skipped=tuple(basket.lines), best_single=None,
                     single_totals={}, split_total=0.0, baseline_total=0.0)

    # Одиночная площадка честна только там, где у неё есть цена на ВСЁ
    # сравнимое. Иначе «лучшей» окажется та, про которую мы меньше знаем.
    venues = set.intersection(*[{p.venue for p in c.prices} for _l, _u, c in priced])
    totals = {}
    for venue in venues:
        totals[venue] = sum(
            units * next(p.median for p in c.prices if p.venue == venue)
            for _l, units, c in priced)
    best_single = min(totals, key=lambda v: (totals[v], v)) if totals else None
    baseline_total = totals.get(best_single, 0.0)

    lines = []
    for line, units, c in priced:
        cheapest = c.cheapest
        base = (units * next(p.median for p in c.prices if p.venue == best_single)
                if best_single else units * cheapest.median)
        lines.append(RouteLine(
            key=line.typical_key, unit=c.unit, units=units, venue=cheapest.venue,
            unit_price=cheapest.median, cost=units * cheapest.median,
            baseline_cost=base))
    lines.sort(key=lambda r: -r.saving)

    return Route(lines=tuple(lines), skipped=tuple(skipped),
                 best_single=best_single, single_totals=totals,
                 split_total=sum(r.cost for r in lines),
                 baseline_total=baseline_total)


@dataclass(frozen=True)
class VenueScore:
    """8.7: место площадки по цене и, если оценки есть, по качеству."""

    venue: str
    price_index: float        # 1.0 — цена как у всех; 0.9 — на 10% дешевле
    price_score: float        # 0..1, где 1 — самая дешёвая из сравниваемых
    total: float              # итог по составляющим из basis
    basis: tuple              # что вошло в итог: («цена»,) или с оценками
    quality: int = None       # звёзды пользователя, 1–5
    ambience: int = None
    keys: int = 0             # на скольких ключах посчитан индекс

    @property
    def rated(self):
        return self.quality is not None or self.ambience is not None


def _stars(value):
    """Звёзды 1–5 → 0..1, чтобы складывать с ценовой долей."""
    return (value - 1) / 4


def price_index(history, groups=None, min_observations=MIN_OBSERVATIONS,
                min_venues=MIN_VENUES):
    """→ {площадка: (индекс, на скольких ключах)}.

    Индекс — медиана отношения «цена этой площадки / медиана по площадкам» по
    всем сравнимым ключам. Медиана отношений, а не отношение сумм: сумма по
    ключам с разными единицами (₽/кг и ₽/шт) бессмысленна, а отношение внутри
    ключа безразмерно и складывается честно.
    """
    ratios = defaultdict(list)
    for c in compare(history, groups=groups, min_observations=min_observations,
                     min_venues=min_venues):
        mid = statistics.median([p.median for p in c.prices])
        if not mid:
            continue
        for p in c.prices:
            ratios[p.venue].append(p.median / mid)
    return {v: (statistics.median(r), len(r)) for v, r in ratios.items()}


def rank(history, settings=None, groups=None, min_keys=MIN_KEYS_FOR_RANK,
         **kwargs):
    """→ площадки, лучшая первой.

    Площадка, у которой сравнимых ключей меньше `min_keys`, в ранг не попадает
    вовсе. Это не придирка: по одному совпавшему ключу «Магнит дешевле Ленты»
    звучит так же уверенно, как по сорока, и отличить их пользователь не может.

    Из чего сложился итог, видно в `basis` — показывать его обязательно:
    «первое место по цене» и «первое место по цене с качеством» отвечают на
    разные вопросы.
    """
    index = {v: (idx, n) for v, (idx, n) in
             price_index(history, groups=groups, **kwargs).items() if n >= min_keys}
    if not index:
        return []

    lo = min(idx for idx, _n in index.values())
    hi = max(idx for idx, _n in index.values())
    span = hi - lo

    ratings = {v: (settings.rating(v) if settings else (None, None)) for v in index}

    # Оценка участвует в итоге, только если она есть У ВСЕХ ранжируемых
    # площадок. Иначе сравнение перестаёт быть сравнением: площадка без
    # оценок считалась бы по одной цене, и выставленные пользователем пять
    # звёзд ОПУСКАЛИ бы оценённую площадку ниже неоценённой. Проставить
    # недостающие звёзды нельзя — это данные пользователя, а не наши, —
    # поэтому недостающая оценка выключает свою составляющую для всех.
    use_quality = all(q is not None for q, _a in ratings.values())
    use_ambience = all(a is not None for _q, a in ratings.values())

    basis = ("цена",)
    if use_quality:
        basis += ("качество",)
    if use_ambience:
        basis += ("обстановка",)

    out = []
    for venue, (idx, keys) in index.items():
        score = 1.0 if span == 0 else (hi - idx) / span
        quality, ambience = ratings[venue]
        parts = [score]
        if use_quality:
            parts.append(_stars(quality))
        if use_ambience:
            parts.append(_stars(ambience))
        out.append(VenueScore(
            venue=venue, price_index=idx, price_score=score,
            total=sum(parts) / len(parts), basis=basis,
            quality=quality, ambience=ambience, keys=keys))
    out.sort(key=lambda s: (-s.total, s.venue))
    return out
