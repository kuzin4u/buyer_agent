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

from .sku import UNKNOWN

RE_TOKEN = re.compile(r"[А-ЯЁA-Z]{3,}")


@dataclass
class Coverage:
    total: int = 0
    excluded: int = 0
    matched: int = 0
    weighted: int = 0
    with_pack: int = 0
    brand_hit: int = 0
    by_source: Counter = field(default_factory=Counter)
    unmatched: Counter = field(default_factory=Counter)
    unit_skus: dict = field(default_factory=dict)
    bulk_skus: dict = field(default_factory=dict)

    @property
    def analysable(self):
        return self.total - self.excluded

    @property
    def unit_items(self):
        """Штучные позиции с определённой фасовкой — база для словаря брендов."""
        return self.with_pack - self.weighted


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
            continue
        if item.group is None:
            cov.unmatched[item.name] += 1
            continue
        cov.matched += 1
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
    """Очередь на пополнение brands.json: топ префиксов без бренда (SPEC §6).

    Префикс берётся эвристикой — первый токен названия длиной от трёх букв,
    не совпавший ни с одним правилом словаря. Эвристика черновая и будет
    уточнена в С6 вместе с панелью; для очереди её достаточно.
    """
    counter = Counter()
    for item in run.items:
        if item.weighted or item.pack_source is None or item.brand != UNKNOWN:
            continue
        for token in RE_TOKEN.findall(item.name)[1:2]:
            counter[token] += 1
    return counter.most_common(limit)


def unmatched_names(run, cov=None, limit=15):
    """Очередь на пополнение categories.json."""
    cov = cov or coverage(run)
    return cov.unmatched.most_common(limit)
