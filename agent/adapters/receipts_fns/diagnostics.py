"""Диагностика прогона: покрытие, очереди на пополнение, сравнимость.

Это не отладочный вывод для разработчика, а часть продукта (README): словарь
брендов и товарные группы пополняются всё время жизни агента, и обе очереди
должны быть видны пользователю с возможностью пополнить конфиг одним кликом
(SPEC §8.11). Здесь считается то, что панель покажет.
"""

import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

from ...history import DAYS_IN_MONTH, PRICE_WINDOW_MONTHS
from .sku import UNKNOWN

RE_TOKEN = re.compile(r"[А-ЯЁA-Z]{3,}")


@dataclass
class Coverage:
    total: int = 0
    excluded: int = 0
    matched: int = 0
    nonfood: int = 0
    nonfood_money: float = 0.0
    weighted: int = 0
    with_pack: int = 0
    brand_hit: int = 0
    by_source: Counter = field(default_factory=Counter)
    unmatched: Counter = field(default_factory=Counter)
    unmatched_money: Counter = field(default_factory=Counter)
    matched_money: float = 0.0
    excluded_money: float = 0.0
    unit_skus: dict = field(default_factory=dict)
    bulk_skus: dict = field(default_factory=dict)

    @property
    def analysable(self):
        return self.total - self.excluded

    @property
    def matched_all(self):
        """Отнесено к любой товарной группе — определение контрольной цифры ТЗ."""
        return self.matched + self.nonfood

    @property
    def unit_items(self):
        """Штучные позиции с определённой фасовкой — база для словаря брендов."""
        return self.with_pack - self.weighted

    @property
    def unmatched_total(self):
        return sum(self.unmatched_money.values())

    @property
    def money(self):
        """Все деньги анализируемых позиций."""
        return self.matched_money + self.nonfood_money + self.unmatched_total

    @property
    def unmatched_share(self):
        return self.unmatched_total / self.money if self.money else 0.0


def coverage(run, segments=None):
    """Покрытие по позициям продуктового ядра.

    По умолчанию считается по тем ярусам, что включены в прогон, и НЕ включает
    разобранные безымянные чеки: они участвуют в контроле трат и в динамике цен
    по SKU, но контрольные цифры ТЗ (839 чеков, 75,6%, 58,6%) построены на
    продуктовом ядре, и смешивать одно с другим нельзя.
    """
    segments = segments or run.food_tiers
    cov = Coverage()
    for item in run.items:
        if item.segment not in segments:
            continue
        cov.total += 1
        if item.excluded:
            cov.excluded += 1
            cov.excluded_money += item.amount
            continue
        if item.group is None:
            cov.unmatched[item.name] += 1
            cov.unmatched_money[item.name] += item.amount
            continue
        # Продуктовое и непродуктовое считаются РАЗДЕЛЬНО (DECISIONS Р-11):
        # иначе непродуктовые категории задирают «покрытие групп» и прячут
        # ровно ту дыру, ради которой это покрытие меряется. Но общий итог
        # сохраняется: контрольные 75,6% ТЗ считались по ВСЕМ группам, включая
        # хозтовары и автотовары, и сопоставимость с ними рвать нельзя.
        if item.food_group:
            cov.matched += 1
            cov.matched_money += item.amount
        else:
            cov.nonfood += 1
            cov.nonfood_money += item.amount
        if item.weighted:
            cov.weighted += 1
            cov.with_pack += 1
            cov.by_source[item.weighted_reason] += 1
            cov.bulk_skus.setdefault(item.key, []).append(item)
            continue
        if item.pack_source is None:
            continue
        cov.with_pack += 1
        cov.by_source[item.pack_source] += 1
        if item.brand != UNKNOWN:
            cov.brand_hit += 1
        cov.unit_skus.setdefault(item.key, []).append(item)
    return cov


def comparable_skus(skus, min_observations):
    """SKU, у которых цена сопоставима МЕЖДУ магазинами.

    Условие: не меньше двух магазинов, и в каждом не меньше min_observations
    наблюдений — тот же порог, что у медианы (SPEC §5). Ниже порога это шум,
    а не тренд, и порог мы не снижаем (docs/DECISIONS.md Р-2).
    """
    out = {}
    for key, items in skus.items():
        by_venue = defaultdict(list)
        for it in items:
            by_venue[it.venue].append(it.unit_price)
        enough = {v: prices for v, prices in by_venue.items()
                  if len(prices) >= min_observations}
        if len(enough) >= 2:
            out[key] = {v: statistics.median(p) for v, p in enough.items()}
    return out


def comparability(run, cov=None):
    """Метрика штатного прогона. Штучные и весовые считаются РАЗДЕЛЬНО.

    Смешивать их в одно число значит прятать разную природу атома: у весового
    ключ — название, у штучного — бренд·жирность·фасовка (Р-3).
    """
    cov = cov or coverage(run)
    m = run.rules.min_observations
    return {
        "min_observations": m,
        "unit": (len(comparable_skus(cov.unit_skus, m)), len(cov.unit_skus)),
        "bulk": (len(comparable_skus(cov.bulk_skus, m)), len(cov.bulk_skus)),
    }


def brandless_prefixes(run, limit=20):
    """Очередь по ОБЪЁМУ: топ токенов у позиций без распознанной марки (§6).

    Префикс берётся эвристикой — первый токен названия длиной от трёх букв, не
    совпавший ни с одним правилом словаря. Эвристику обещали уточнить в С6; она
    уточнена не здесь, а рядом: `brand_candidates` отвечает на другой вопрос — не
    «чего больше всего», а «что даст ответ». Наверху этой очереди стоят «КУР»,
    «ПЕСОК», «СЫР» — слова товара, а не марки, и это её честный предел: она
    показывает, где сосредоточена масса неразобранного, а не что размечать.
    """
    counter = Counter()
    for item in run.items:
        if item.weighted or item.pack_source is None or item.brand != UNKNOWN:
            continue
        for token in RE_TOKEN.findall(item.name)[1:2]:
            counter[token] += 1
    return counter.most_common(limit)


@dataclass
class QueueRow:
    name: str
    purchases: int
    amount: float


def _rows(cov, names):
    return [QueueRow(name=n, purchases=cov.unmatched[n],
                     amount=cov.unmatched_money[n]) for n in names]


def queue_by_money(cov, limit=15):
    """Очередь А — непродуктовые категории. Сортировка по деньгам.

    Цель — корректный контроль трат, поэтому мера рублёвая. Наверху окажутся
    разовые дорогие покупки: гриль, шина, кухонная машина. Очередь по числу
    покупок их не увидит никогда, а они и есть основная масса денег.
    Порога по числу покупок здесь нет намеренно: разовая покупка — норма для
    непродуктового, и именно её надо отнести в свою категорию.
    """
    names = sorted(cov.unmatched_money, key=lambda n: -cov.unmatched_money[n])
    return _rows(cov, names[:limit])


def queue_by_purchases(cov, min_observations, limit=15):
    """Очередь Б — продуктовые группы. Сортировка по числу покупок.

    Цель — покрытие для медиан, поэтому мера — наблюдения. Ниже порога
    min_observations медиану всё равно не построить, так что дальше этой
    границы разбирать нечего.

    ВАЖНО: частота — это порядок разбора, а не правило отнесения. Регулярно
    покупают и расходники: МАСЛО TAIF 5W40 куплено 9 раз. Очередь говорит,
    что смотреть раньше, а не куда относить.
    """
    names = [n for n, c in cov.unmatched.items() if c >= min_observations]
    names.sort(key=lambda n: (-cov.unmatched[n], -cov.unmatched_money[n]))
    return _rows(cov, names[:limit])


def queue_b_size(cov, min_observations):
    """Сколько всего в очереди Б: названий и рублей."""
    names = [n for n, c in cov.unmatched.items() if c >= min_observations]
    return len(names), sum(cov.unmatched_money[n] for n in names)


def unmatched_names(run, cov=None, limit=15):
    """Совместимость: очередь Б без порога."""
    cov = cov or coverage(run)
    return cov.unmatched.most_common(limit)


@dataclass
class BrandCandidate:
    """Название, которое само по себе уже сравнимо между магазинами.

    Зачем отдельная очередь. Соблазнительно считать очередью брендов смешанные
    ключи, набравшие наблюдений: их 33, и кажется, что каждому не хватает одной
    марки. Это неправда — внутри такого ключа лежат разные товары («— · —% ·
    0,1 кг» собрал шоколад из Перу, белёвскую пастилу и жгучий перец), и правило
    бренда не разблокирует пул, а расщепляет его на осколки ниже порога.

    Здесь условие проверяемое, а не предполагаемое: ЭТО ЖЕ название встречается
    не меньше `min_observations` раз в каждом из не менее двух магазинов. Значит
    размеченное, оно даст сравнимый товар — не «вероятно», а точно, потому что
    наблюдения уже есть и они про один и тот же товар.
    """

    name: str
    group: str
    unit: str
    purchases: int
    venues: int
    spread: float             # разрыв медиан между магазинами, ₽ за единицу
    in_basket: bool = False   # группа входит в недельную корзину
    in_month: bool = False


def brand_candidates(run, min_observations=None, basket_groups=(),
                     month_groups=(), limit=20, window=PRICE_WINDOW_MONTHS,
                     asof=None):
    """Очередь по ЭФФЕКТУ: что разметить, чтобы прибавился сравнимый товар.

    Порядок — сначала то, что стоит в недельной корзине пользователя, потом в
    месячной, потом по денежному разрыву между магазинами. Это и есть порядок
    полезности: строка недельной корзины — прямой ответ на главный вопрос,
    а разрыв в рублях — то, что этот ответ стоит.

    **Окно наблюдений то же, что у сравнения (П-8).** Очередь обещает
    проверяемое: «размеченное, это название даст сравнимый товар». Обещание
    держится, только если наблюдения считаются по тому же правилу, по которому
    потом строится сравнение. Пока правила были разные, очередь предлагала
    разметить «ДСК ОГУРЦЫ» — 11 покупок в двух магазинах, но последняя восемь
    лет назад, — и разметка не давала ничего.
    """
    min_observations = min_observations or run.rules.min_observations
    asof = asof or max((item.dt for item in run.items), default="")
    by_name = defaultdict(list)
    for item in run.items:
        if item.weighted or item.pack_source is None or item.key is None:
            continue
        if item.brand != UNKNOWN:
            continue
        if window is not None and item.dt and asof:
            age = (date.fromisoformat(asof[:10])
                   - date.fromisoformat(item.dt[:10])).days / DAYS_IN_MONTH
            if age > window:
                continue
        by_name[(item.name, item.key.group, item.key.unit)].append(item)

    rows = []
    for (name, group, unit), items in by_name.items():
        by_venue = defaultdict(list)
        for item in items:
            if item.venue and item.unit_price:
                by_venue[item.venue].append(item.unit_price)
        enough = [statistics.median(p) for p in by_venue.values()
                  if len(p) >= min_observations]
        if len(enough) < 2:
            continue
        rows.append(BrandCandidate(
            name=name, group=group, unit=unit, purchases=len(items),
            venues=len(enough), spread=max(enough) - min(enough),
            in_basket=group in set(basket_groups),
            in_month=group in set(month_groups)))
    rows.sort(key=lambda r: (not r.in_basket, not r.in_month, -r.spread, r.name))
    return rows[:limit] if limit else rows


def pack_sources(run, segments=None):
    """Чем определена фасовка — сводка по всем позициям (SPEC §8.11).

    Требование §8.11 буквально: показать, чем определена фасовка у КАЖДОЙ
    позиции. Сводка — вход в эту таблицу, сама таблица строится `positions`.
    """
    segments = segments or run.food_tiers
    counter = Counter()
    for item in run.items:
        if item.segment not in segments:
            continue
        counter[item.status] += 1
    return counter.most_common()


def positions(run, status=None, group=None, search=None, brandless=False,
              segments=None, offset=0, limit=100):
    """Позиции с причиной, по которой фасовка определена так, а не иначе.

    Возвращает (строки, сколько всего подошло). Страницами, потому что позиций
    почти двадцать тысяч, а ответ нужен про конкретную: пользователь приходит
    сюда из очереди, с названием в руках.
    """
    segments = segments or run.food_tiers
    needle = (search or "").upper().replace("Ё", "Е").strip()
    found = []
    for item in run.items:
        if item.segment not in segments:
            continue
        if status and item.status != status:
            continue
        if group and item.group != group:
            continue
        if brandless and (item.brand != UNKNOWN or item.weighted
                          or item.pack_source is None):
            continue
        if needle and needle not in item.match.upper():
            continue
        found.append(item)
    return found[offset:offset + limit], len(found)


def group_size(run, group, segments=None):
    """Сколько позиций отнесено к группе. Локальный эффект правила категории.

    Главная мера (`agent.reach`) отвечает на вопрос про магазины, и правило,
    переносящее молочный шоколад из «молока» в «сладкое», её не двигает — оно
    исправляет отнесение, а не покрытие. Без этого числа такое правило выглядело
    бы бесполезным, и пользователь откатил бы верное исправление.
    """
    segments = segments or run.food_tiers
    return sum(1 for item in run.items
               if item.segment in segments and item.group == group)
