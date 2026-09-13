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
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date

from ..history import DAYS_IN_MONTH, PRICE_WINDOW_MONTHS

#: наблюдений у одной площадки, чтобы её медиана что-то значила (Р-2)
MIN_OBSERVATIONS = 3
#: площадок, чтобы было что с чем сравнивать
MIN_VENUES = 2
#: сравнимых ключей, чтобы у площадки был ценовой ранг, а не одно совпадение
MIN_KEYS_FOR_RANK = 5

#: Доля от самого частого товара группы, с которой товар считается регулярным, а
#: не случайным. Нужна там, где типичный товар сравнить не с чем, а другая ваша
#: марка в той же группе сравнима: подставить её — не подмена привычки, если вы
#: берёте её сопоставимо часто (П-7 закрыт решением Р-25).
#:
#: Почему треть. Порог взят из плато: на датасете значения от 0,33 до 0,20 дают
#: ОДИН И ТОТ ЖЕ ответ (недельной корзине +2 строки, месячной +4), то есть выбор
#: не стоит на лезвии. Встреченные доли — 82%, 68%, 42%, 39%, дальше обрыв ниже
#: 20%. Снизу порог подпёрт самой сравнимостью: у такого товара уже есть не меньше
#: трёх покупок в каждом из двух магазинов, то есть минимум шесть (Р-2).
REGULAR_SHARE_OF_TYPICAL = 1 / 3


def months_between(earlier, later):
    """Возраст в месяцах между двумя отметками времени."""
    if not earlier or not later:
        return None
    return max((date.fromisoformat(later[:10])
                - date.fromisoformat(earlier[:10])).days / DAYS_IN_MONTH, 0.0)


@dataclass(frozen=True)
class VenuePrice:
    venue: str
    median: float
    observations: int
    #: Когда этот товар покупался у этой площадки в последний раз. Нужен, чтобы
    #: возраст цены можно было назвать по каждой площадке, а не только по товару.
    last_ts: str = None


@dataclass(frozen=True)
class KeyComparison:
    """Цена одного ключа у всех площадок, где её можно померить."""

    key: object
    unit: str
    prices: tuple             # VenuePrice, от дешёвой к дорогой
    #: Конец истории — точка, от которой считается возраст. Хранится в самом
    #: сравнении: без него «43 месяца» нельзя ни посчитать, ни проверить.
    asof: str = None

    @property
    def last_ts(self):
        """Самая свежая покупка этого товара где угодно."""
        stamps = [p.last_ts for p in self.prices if p.last_ts]
        return max(stamps) if stamps else None

    @property
    def months_old(self):
        return months_between(self.last_ts, self.asof)

    @property
    def stale(self):
        """Цена старше окна сравнения — при окне по умолчанию не бывает.

        Остаётся значимым, когда окно расширено вызовом: тогда признак честно
        говорит, что сравнение вышло за пределы современников.
        """
        months = self.months_old
        return months is not None and months > PRICE_WINDOW_MONTHS

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


def _by_key_venue(history, keys=None, groups=None, window=PRICE_WINDOW_MONTHS,
                  asof=None):
    """→ ({ключ: {площадка: [цены]}}, {ключ: {площадка: последняя дата}}).

    `window` — окно наблюдений в месяцах. Оно не про «старое плохо», а про то,
    что сравнение вообще имеет смысл только между современниками: см. `compare`.
    """
    asof = asof or history.span()[1]
    prices = defaultdict(lambda: defaultdict(list))
    last = defaultdict(dict)
    for e in history.events:
        if e.key is None or e.unit_price is None or not e.venue:
            continue
        if keys is not None and e.key not in keys:
            continue
        if groups and e.key.group not in groups:
            continue
        if window is not None and (months_between(e.ts, asof) or 0) > window:
            continue
        prices[e.key][e.venue].append(e.unit_price)
        if e.ts > last[e.key].get(e.venue, ""):
            last[e.key][e.venue] = e.ts
    return prices, last


def compare(history, keys=None, groups=None, min_observations=MIN_OBSERVATIONS,
            min_venues=MIN_VENUES, limit=None, include_blended=False,
            window=PRICE_WINDOW_MONTHS):
    """→ ключи, цену которых можно сравнить между площадками.

    **Сравниваются только современники.** Медиана считается по наблюдениям не
    старше `window` месяцев — у всех площадок одинаково. Без этого сравнение
    измеряет не разницу магазинов, а разницу лет: медиана «Ленты» по всей истории
    — это 2023 год, медиана «Глобуса» — сегодняшний, и «в Ленте дешевле» означает
    лишь то, что деньги подешевели.

    Найдено измерением (П-8). На датасете сужение окна роняет экономию недельной
    корзины 334 → 192 → 68 ₽ (вся история → 36 мес → 24 мес). Разница между
    площадками так себя не ведёт: она бы держалась, теряя товары, но не величину.
    Так ведёт себя инфляция. До окна маршрут вёл за луком во «ВкусВилл» по цене,
    которой девять лет.

    Цена правила: сравнимых товаров 25 → 14. Это не потеря ответа, а отказ от
    ответа, которого не было: сравнить цену 2017 года с ценой 2026-го нельзя,
    сколько её ни показывай.
    

    Сортировка по абсолютному разрыву: наверху то, где выбор площадки стоит
    дороже всего. Процент показывается рядом, но не сортирует, — правило то
    же, что в §8.5.

    Ключи с нераспознанной маркой (ItemKey.blended) по умолчанию исключены.
    Без отсева верх списка занимает «заморозка 0,08 кг: 531 ₽/кг у одних,
    2 000 ₽/кг у других» — это разные товары, а не разные цены, и вести
    человека в магазин по такому расчёту нельзя.
    """
    asof = history.span()[1]
    by_key, last = _by_key_venue(history, keys, groups, window=window, asof=asof)
    out = []
    for key, by_venue in by_key.items():
        if key.blended and not include_blended:
            continue
        rows = [VenuePrice(venue=v, median=statistics.median(p), observations=len(p),
                           last_ts=last[key].get(v))
                for v, p in by_venue.items() if len(p) >= min_observations]
        if len(rows) < min_venues:
            continue
        rows.sort(key=lambda r: (r.median, r.venue))
        out.append(KeyComparison(key=key, unit=key.unit, prices=tuple(rows),
                                 asof=asof))
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
    #: Типичный товар группы, вместо которого сравнивали. Не None означает, что
    #: сравнение шло по ДРУГОЙ вашей регулярной марке, и показывать это
    #: обязательно: иначе человек поедет за тем, чего не собирался брать.
    instead_of: object = None
    share_of_typical: float = None
    #: Возраст цены, по которой посчитана строка. Печатается всегда, когда цена
    #: старше окна свежести: маршрут, ведущий в магазин по цене двухлетней
    #: давности, обязан об этом сказать (П-8).
    last_ts: str = None
    asof: str = None

    @property
    def months_old(self):
        return months_between(self.last_ts, self.asof)

    @property
    def stale(self):
        months = self.months_old
        return months is not None and months > PRICE_WINDOW_MONTHS

    @property
    def saving(self):
        return self.baseline_cost - self.cost

    @property
    def substituted(self):
        return self.instead_of is not None


@dataclass(frozen=True)
class Route:
    """Маршрут экономии против покупки всего в одном месте."""

    lines: tuple
    skipped: tuple            # строки, которые сравнить не удалось вовсе
    best_single: str
    single_totals: dict       # {площадка: сумма сравнимой части корзины}
    split_total: float
    baseline_total: float
    #: Строки, которые сравнить между магазинами можно, но у одиночной площадки
    #: их нет. В экономию они не входят: оба сценария обязаны считаться по одним
    #: и тем же строкам, иначе разность перестаёт быть экономией.
    outside_baseline: tuple = ()

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
    def considered(self):
        return len(self.lines) + len(self.skipped) + len(self.outside_baseline)

    @property
    def covered(self):
        """Доля корзины, которую удалось развести: без неё экономия — половина
        правды. Пять процентов корзины, разложенные идеально, это не экономия."""
        return len(self.lines) / self.considered if self.considered else 0.0

    @property
    def substituted(self):
        """Строки, сравнённые по другой вашей регулярной марке (Р-25)."""
        return tuple(line for line in self.lines if line.substituted)

    @property
    def stale(self):
        """Строки, посчитанные по цене старше окна свежести (П-8)."""
        return tuple(line for line in self.lines if line.stale)


def _units(line):
    """Сколько базовых единиц стоит за строкой корзины."""
    if not line.unit_price:
        return None
    return line.amount / line.unit_price


def key_frequencies(history):
    """→ {группа: {ключ: сколько раз брался}}. Чем закрывается потребность."""
    counts = defaultdict(Counter)
    for event in history.events:
        if event.key is not None:
            counts[event.key.group][event.key] += 1
    return counts


def regular_alternative(typical, comparisons, frequencies,
                        min_share=REGULAR_SHARE_OF_TYPICAL):
    """Другая ваша регулярная марка той же группы, которую МОЖНО сравнить.

    «Типичный» и «регулярный» — не одно и то же. Если две марки сметаны берутся
    поровну, типичная одна, а регулярны обе, и сравнить цену по второй — не
    подмена привычки: человек её и так покупает. Это и закрывает П-7.

    Два условия, и оба необходимы:

    *Та же базовая единица.* ₽/кг и ₽/шт несопоставимы (SPEC §5), и подставить
    одно вместо другого значит посчитать бессмысленное число. На датасете такой
    случай есть, и он отсеивается здесь.

    *Сопоставимая частота.* Ниже `min_share` от самого частого товара группы это
    уже не ваша вторая марка, а разовая покупка, и вести за ней в другой магазин
    нельзя — ровно та негодная замена, которую запрещает §8.10.
    """
    if typical is None:
        return None, None
    ranked = frequencies.get(typical.group)
    if not ranked:
        return None, None
    top = ranked.most_common(1)[0][1]
    if not top:
        return None, None
    for key, count in ranked.most_common():
        if key == typical or key not in comparisons:
            continue
        if key.unit != typical.unit:
            continue
        share = count / top
        if share < min_share:
            break          # дальше по убыванию частоты только реже
        return key, share
    return None, None


def smart_basket(history, basket, min_observations=MIN_OBSERVATIONS,
                 min_venues=MIN_VENUES, min_share=REGULAR_SHARE_OF_TYPICAL,
                 window=PRICE_WINDOW_MONTHS):
    """Корзина, разведённая по площадкам, против лучшей одиночной площадки.

    База сравнения — не «текущие траты», а лучшая ОДИНОЧНАЯ площадка: вопрос
    пользователя звучит «стоит ли ехать в два места вместо одного», и честный
    ответ сравнивает именно эти два сценария.

    Строки, которые сравнить не удалось, в экономию не засчитываются и уходят
    в skipped. Иначе достаточно было бы не найти цену у дорогой площадки,
    чтобы «сэкономить».

    Если типичный товар группы сравнить не с чем, берётся другая РЕГУЛЯРНАЯ
    марка той же группы — см. `regular_alternative`. Подстановка помечается в
    строке (`instead_of`) и показывается всегда: сравнение по другому товару —
    всё ещё ответ на вопрос «где дешевле», но человек обязан знать, по какому.
    """
    # Сравнивается всё, чем вообще закрываются группы корзины, а не только
    # типичные товары: иначе регулярную марку не на что было бы подставить.
    frequencies = key_frequencies(history)
    groups = {l.typical_key.group for l in basket.lines if l.typical_key}
    comparisons = {c.key: c for c in compare(
        history, groups=groups, min_observations=min_observations,
        min_venues=min_venues, window=window)}

    priced, skipped = [], []
    for line in basket.lines:
        units = _units(line)
        if units is None:
            skipped.append(line)
            continue
        if line.typical_key in comparisons:
            priced.append((line, units, comparisons[line.typical_key], None, None))
            continue
        alternative, share = regular_alternative(
            line.typical_key, comparisons, frequencies, min_share)
        if alternative is None:
            skipped.append(line)
        else:
            priced.append((line, units, comparisons[alternative],
                           line.typical_key, share))

    if not priced:
        return Route(lines=(), skipped=tuple(basket.lines), best_single=None,
                     single_totals={}, split_total=0.0, baseline_total=0.0)

    # Оба сценария — «в одном месте» и «врозь» — обязаны считаться по ОДНИМ И
    # ТЕМ ЖЕ строкам, иначе разность перестаёт быть экономией. Поэтому сначала
    # выбирается одиночная площадка, а потом маршрут сужается до строк, которые
    # у неё есть.
    #
    # Раньше здесь стояло пересечение площадок по ВСЕМ строкам, и пока корзина
    # была мала, оно существовало. С подстановкой регулярных марок строк стало
    # больше, пересечение опустело, база обнулилась — и экономия вышла
    # отрицательной: «врозь» сравнивалось с нулём. Число выглядело правдоподобно,
    # и это худший вид ошибки (Р-21).
    covers = defaultdict(list)
    for index, row in enumerate(priced):
        for price in row[2].prices:
            covers[price.venue].append(index)
    totals = {}
    for venue, indexes in covers.items():
        totals[venue] = sum(
            priced[i][1] * next(p.median for p in priced[i][2].prices
                                if p.venue == venue)
            for i in indexes)
    # Сначала покрытие, потом цена: площадка, про которую мы знаем больше, честнее
    # дешёвой, про которую мы знаем одну строку.
    best_single = max(covers, key=lambda v: (len(covers[v]), -totals[v], v)) \
        if covers else None
    outside = []
    if best_single is not None:
        inside = set(covers[best_single])
        outside = [priced[i][0] for i in range(len(priced)) if i not in inside]
        priced = [priced[i] for i in sorted(inside)]
    baseline_total = totals.get(best_single, 0.0)

    lines = []
    for line, units, c, instead_of, share in priced:
        cheapest = c.cheapest
        base = (units * next(p.median for p in c.prices if p.venue == best_single)
                if best_single else units * cheapest.median)
        lines.append(RouteLine(
            key=c.key, unit=c.unit, units=units, venue=cheapest.venue,
            unit_price=cheapest.median, cost=units * cheapest.median,
            baseline_cost=base, instead_of=instead_of, share_of_typical=share,
            # Возраст берётся у ТОЙ площадки, куда строка ведёт: маршрут
            # советует ехать именно туда, и важна свежесть именно её цены.
            last_ts=cheapest.last_ts, asof=c.asof))
    lines.sort(key=lambda r: -r.saving)

    return Route(lines=tuple(lines), skipped=tuple(skipped),
                 best_single=best_single, single_totals=totals,
                 split_total=sum(r.cost for r in lines),
                 baseline_total=baseline_total, outside_baseline=tuple(outside))


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
                min_venues=MIN_VENUES, window=PRICE_WINDOW_MONTHS):
    """→ {площадка: (индекс, на скольких ключах)}.

    Индекс — медиана отношения «цена этой площадки / медиана по площадкам» по
    всем сравнимым ключам. Медиана отношений, а не отношение сумм: сумма по
    ключам с разными единицами (₽/кг и ₽/шт) бессмысленна, а отношение внутри
    ключа безразмерно и складывается честно.
    """
    ratios = defaultdict(list)
    for c in compare(history, groups=groups, min_observations=min_observations,
                     min_venues=min_venues, window=window):
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
