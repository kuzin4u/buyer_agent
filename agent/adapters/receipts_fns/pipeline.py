"""Прогон конвейера: чеки → нормализованные позиции → история (SPEC §11).

Конвейер — чистая функция от (конфиги, чеки). Ничего не пишет на диск и не
материализуется в таблицу: порядок правил значим, и запросам мимо конвейера
взяться неоткуда (docs/DECISIONS.md Р-4).
"""

from dataclasses import dataclass, field

from ...history import Event, History, Outlay
from . import normalize as N
from .rules import Rules
from .sku import bulk_sku, unit_sku

FOOD_TIERS = ("food_core", "food_candidate")

#: Чек без опознанного продавца, прошедший разбор по составу (SPEC §7).
#: Участвует в контроле трат и в динамике цен по SKU, но НЕ в сравнении
#: магазинов — сравнивать не с чем.
UNKNOWN_VENUE = "Не определён"
UNKNOWN_FOOD = "unknown_food"


@dataclass
class NormalizedItem:
    """Одна позиция после конвейера — включая те, что не дошли до SKU.

    Молча выбрасывать нельзя (SPEC §5): каждая позиция несёт причину, по
    которой попала или не попала в ценовой анализ. Это то, что показывает
    панель диагностики (§8.11).
    """

    dt: str
    venue: str
    segment: str
    raw: str
    name: str       # отображаемое название: Ё сохранена
    match: str      # сопоставительная форма: Ё сложена в Е
    qty: float
    price: float
    amount: float
    excluded: bool = False
    group: str = None
    dept: str = None
    weighted_reason: str = None
    unit: str = None
    pack: float = None
    pack_source: str = None
    brand: str = None
    fat: str = None
    key: object = None
    unit_price: float = None

    @property
    def weighted(self):
        return self.weighted_reason is not None

    @property
    def priced(self):
        """Участвует ли в динамике цен и сравнении магазинов."""
        return self.key is not None

    @property
    def status(self):
        if self.excluded:
            return "исключено"
        if self.group is None:
            return "нет группы"
        if self.weighted:
            return self.weighted_reason
        if self.pack_source:
            return self.pack_source
        return "фасовка не определена"


@dataclass
class Run:
    """Результат прогона. Всё, что нужно диагностике и профилю."""

    rules: Rules
    include_candidates: bool
    receipts: list
    tier_stat: dict = field(default_factory=dict)
    shop_stat: dict = field(default_factory=dict)
    unknown_receipts: list = field(default_factory=list)
    unknown_food_count: int = 0
    unknown_food_sum: float = 0.0
    items: list = field(default_factory=list)
    outlays: list = field(default_factory=list)
    fractional: dict = field(default_factory=dict)

    @property
    def receipts_count(self):
        return len(self.receipts)

    @property
    def receipts_sum(self):
        return sum(r.total for r in self.receipts)

    @property
    def food_tiers(self):
        return ({"food_core", "food_candidate"} if self.include_candidates
                else {"food_core"})


class Pipeline:
    def __init__(self, config, include_candidates=False):
        self.rules = Rules(config)
        self.include_candidates = include_candidates

    def run(self, receipts):
        rules = self.rules
        food_tiers = ({"food_core", "food_candidate"} if self.include_candidates
                      else {"food_core"})

        out = Run(rules=rules, include_candidates=self.include_candidates,
                  receipts=receipts)
        tier_stat = {}
        shop_stat = {}
        classified = []

        for r in receipts:
            canon, tier = N.classify_shop(rules, r.shop)
            classified.append((canon, tier, r))
            slot = tier_stat.setdefault(tier, [0, 0.0])
            slot[0] += 1
            slot[1] += r.total
            if tier in food_tiers:
                slot2 = shop_stat.setdefault(canon, [0, 0.0])
                slot2[0] += 1
                slot2[1] += r.total
            elif tier == "unknown":
                out.unknown_receipts.append(r)

        out.tier_stat = tier_stat
        out.shop_stat = shop_stat

        # Чеки без опознанного продавца классифицируются по составу позиций:
        # фильтр по имени магазина терял их целиком (SPEC §7).
        thr = rules.food_share_threshold
        resolved = set()
        for idx, (_canon, tier, r) in enumerate(classified):
            if tier != "unknown":
                continue
            total = sum(i.total for i in r.items) or 1
            food = sum(i.total for i in r.items
                       if N.classify_item(rules, N.fold(rules, N.clean(rules, i.name))))
            if food / total >= thr:
                out.unknown_food_count += 1
                out.unknown_food_sum += r.total
                resolved.add(idx)

        # Весовость считается по продуктовому ядру, как и раньше: подмешивать
        # сюда разрешённые безымянные значит менять признак задним числом.
        out.fractional = N.fractional_names(rules, receipts, set(shop_stat))

        for idx, (canon, tier, r) in enumerate(classified):
            txn = str(idx)
            if tier in food_tiers:
                venue, segment = canon, tier
            elif idx in resolved:
                venue, segment = UNKNOWN_VENUE, UNKNOWN_FOOD
            else:
                # Непродуктовое и неразобранное: в ценовой анализ не идёт, но
                # деньги учитываются — это тоже траты пользователя (SPEC §8.8).
                for it in r.items:
                    out.outlays.append(Outlay(
                        txn=txn, ts=r.dt, amount=it.total,
                        venue=canon, segment=tier))
                continue

            for it in r.items:
                item = self._item(venue, segment, r, it, out.fractional)
                out.items.append(item)
                out.outlays.append(Outlay(
                    txn=txn, ts=r.dt, amount=it.total, venue=venue,
                    segment=segment, group=item.group, dept=item.dept))

        return out

    def _item(self, venue, segment, receipt, raw_item, fractional):
        rules = self.rules
        name = N.clean(rules, raw_item.name)
        match = N.fold(rules, name)
        item = NormalizedItem(
            dt=receipt.dt, venue=venue, segment=segment, raw=raw_item.name,
            name=name, match=match, qty=raw_item.qty, price=raw_item.price,
            amount=raw_item.total)

        if N.is_excluded(rules, match):
            item.excluded = True
            return item

        group = N.classify_item(rules, match)
        if group is None:
            return item
        item.group = group["id"]
        item.dept = group.get("dept")

        # весовость — ДО извлечения фасовки (docs/DECISIONS.md Р-3)
        item.weighted_reason = N.weighted_reason(rules, match, fractional)
        if item.weighted_reason:
            item.unit = "kg"
            item.unit_price = raw_item.price      # у весовых цена уже ₽/кг
            item.key = bulk_sku(item.group, match)
            return item

        pack = N.extract_pack(rules, match, item.group)
        if pack is None:
            return item

        item.unit, item.pack, item.pack_source = pack
        item.brand = N.find_brand(rules, match)
        item.fat = N.extract_fat(match)
        item.unit_price = raw_item.price / (item.pack or 1)
        item.key = unit_sku(item.group, item.brand, item.fat, item.unit, item.pack)
        return item


def to_history(run, principal="owner"):
    """Нормализованные позиции → нейтральная история (шов SPEC §8.10).

    Дальше по течению слов «чек», «SKU» и «магазин» уже нет.
    """
    events = [
        Event(ts=i.dt, key=i.key, qty=i.qty, unit_price=i.unit_price,
              amount=i.amount, venue=i.venue)
        for i in run.items if i.priced
    ]
    return History(principal=principal, events=events, outlays=list(run.outlays),
                   source="receipts_fns")
