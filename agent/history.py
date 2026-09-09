"""Нейтральная модель истории — шов между адаптером и профилем (SPEC §8.10).

Здесь намеренно нет слов «чек», «SKU» и «магазин». Адаптер переводит свой
источник в эти типы, profile и matching работают только с ними. Когда появится
второй адаптер, менять придётся его, а не потребителей.

Сейчас адаптер один (SPEC §9: три адаптера НЕ пишем). Поэтому здесь ровно
столько абстракции, сколько нужно для шва, — датаклассы, без реестров и базовых
классов под несуществующие источники.
"""

from dataclasses import dataclass, field

#: базовые единицы измерения, в которых сопоставима цена
UNITS = ("kg", "l", "pcs")


@dataclass(frozen=True)
class ItemKey:
    """Единица, внутри которой цена сопоставима во времени и между площадками.

    kind:  'unit' — фасованная позиция, различители в parts
           'bulk' — весовая: фасовка и производитель неприменимы (SPEC §5)
    group: единица потребности; состав набора выбирается на этом уровне
    parts: различители внутри группы
    unit:  базовая единица цены
    """

    kind: str
    group: str
    parts: tuple
    unit: str
    label: str

    def __post_init__(self):
        assert self.kind in ("unit", "bulk"), self.kind
        assert self.unit in UNITS, self.unit


@dataclass(frozen=True)
class Event:
    """Один факт приобретения."""

    ts: str
    key: ItemKey
    qty: float
    unit_price: float
    amount: float
    venue: str = None

    @property
    def year(self):
        return self.ts[:4]


@dataclass(frozen=True)
class Outlay:
    """Потраченные деньги с адресом: когда, где, на что.

    Отличается от Event охватом. Event — то, у чего есть сопоставимая цена, и
    он нужен для ценового анализа. Outlay — КАЖДЫЙ рубль, включая то, что в
    ценовой анализ не попало: нераспознанные позиции, непродуктовые площадки,
    сделки без опознанного контрагента. Контроль трат строится по Outlay,
    иначе он перестаёт быть контролем трат.

    txn группирует расходы одной сделки — без него не посчитать средний чек.
    """

    txn: str
    ts: str
    amount: float
    venue: str = None
    segment: str = None
    group: str = None
    dept: str = None

    @property
    def year(self):
        return self.ts[:4]

    @property
    def month(self):
        return self.ts[:7]


@dataclass
class History:
    """История одного принципала. Никогда не смешивает двух (SPEC §8.10)."""

    principal: str
    events: list = field(default_factory=list)
    outlays: list = field(default_factory=list)
    source: str = ""

    def __len__(self):
        return len(self.events)

    def groups(self):
        return {e.key.group for e in self.events}

    def span(self):
        """(первая дата, последняя) по всему, что известно."""
        stamps = [o.ts for o in self.outlays] or [e.ts for e in self.events]
        return (min(stamps), max(stamps)) if stamps else (None, None)

    def total_spent(self):
        return sum(o.amount for o in self.outlays)
