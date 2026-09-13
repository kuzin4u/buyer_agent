"""Каталог — то, из чего подбирается позиция (SPEC §8.10).

Каталог здесь один и свой: собственные покупки пользователя. Чужого каталога у
первой версии закупщика нет (SPEC §8.10, последний абзац), поэтому единственный
источник предложений — то, что человек сам уже брал.

Это не обеднение, а единственная гарантия качества, которую можно извлечь из
чеков. Замена дорогой позиции на дешёвую (§8.3) обязана быть «то же качество
дешевле, а не негодная замена» (§8.10), а про качество в чеке нет ни слова.
Зато есть факт: эту позицию человек брал сам, и брал не один раз. Предложить
ему марку, которую он уже покупал три раза за последние два года, — совсем не
то же, что предложить неизвестный товар, который дешевле.

Режим позиции живёт на самой позиции, а не на каталоге: горизонт выводится из
того, ОТКУДА позиция (SPEC §8.10, таблица режимов). Каталог знает свой режим
только потому, что умеет спросить его у своих предложений.
"""

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from . import Horizon, Mode

#: Наблюдений на одну позицию, чтобы её медианная цена что-то значила. Порог
#: общий для всего проекта и снижать его нельзя (docs/DECISIONS.md Р-2).
MIN_OBSERVATIONS = 3

#: Окно, из которого берётся цена предложения, в месяцах. Цена 2017 года не
#: является предложением в 2026-м: обещать по ней экономию — то же самое, что
#: выдавать за цену число, похожее на цену (Р-21).
#: Почему именно два года: на датасете окно в 12 месяцев оставляет 8 групп, где
#: вообще есть из чего выбирать, 24 месяца — 16, 36 месяцев — 19. Удвоение
#: покрытия стоит одного лишнего года, следующий год добавляет уже мало.
PRICE_WINDOW_MONTHS = 24


@dataclass(frozen=True)
class Offer:
    """Одна позиция каталога: чем её можно закрыть и по какой цене.

    `mode` — откуда позиция. Из него, и только из него, выводится горизонт
    подбора (SPEC §8.10): поэтому горизонт нельзя выставить вызовом, его
    неоткуда взять, кроме самого предложения.
    """

    key: object
    unit_price: float         # медиана ₽ за базовую единицу в окне
    observations: int
    mode: Mode
    first_ts: str = None
    last_ts: str = None

    @property
    def unit(self):
        return self.key.unit

    @property
    def group(self):
        return self.key.group

    @property
    def label(self):
        return self.key.label

    @property
    def horizon(self):
        """Горизонт этой позиции. Производная режима, а не параметр."""
        return Horizon.of(self.mode.value)


@dataclass(frozen=True)
class Catalog:
    """Предложения, сгруппированные так, как их спрашивает подбор."""

    offers: dict              # {ItemKey: Offer}
    window: tuple = (None, None)   # (с какой даты, по какую) взяты цены
    source: str = ""

    def __len__(self):
        return len(self.offers)

    def get(self, key):
        return self.offers.get(key)

    def price(self, key):
        offer = self.offers.get(key)
        return offer.unit_price if offer else None

    @property
    def mode(self):
        """Режим каталога — только если он у всех позиций один.

        Смешанный каталог общего режима не имеет, и это не недоработка: когда
        появится каталог Системы рядом с каталогом рынка, единого горизонта у
        них не будет, а будет свой у каждой позиции.
        """
        modes = {o.mode for o in self.offers.values()}
        return next(iter(modes)) if len(modes) == 1 else None

    def alternatives(self, key):
        """Чем ещё закрывается та же потребность — от дешёвой позиции к дорогой.

        Сопоставимо только внутри группы И внутри базовой единицы: сложить
        ₽/кг с ₽/шт нельзя, а значит и сравнить нельзя (SPEC §5).
        """
        return tuple(sorted(
            (o for o in self.offers.values()
             if o.group == key.group and o.unit == key.unit),
            key=lambda o: (o.unit_price, o.label)))

    def groups_with_choice(self):
        """Группы, где есть из чего выбирать: иначе заменять нечем."""
        counts = defaultdict(int)
        for offer in self.offers.values():
            counts[(offer.group, offer.unit)] += 1
        return {k for k, n in counts.items() if n > 1}


def substitutable(key):
    """Можно ли вообще менять эту позицию на другую из той же группы.

    Два запрета, и оба выведены из данных, а не из осторожности.

    *Весовой ключ не заменяется.* У весового товара различитель — само название,
    а не марка (Р-3), поэтому две весовые позиции одной группы — это два разных
    товара, а не две версии одного. На датасете «дешевейшее в группе» без этого
    запрета предлагает заменить ЛУК РЕПЧАТЫЙ на КАПУСТУ БЕЛОКОЧАННУЮ, а
    свиные рёбра — на свиные ноги. Формально это внутри группы. Для человека
    это не замена, а другой ужин.

    *Смешанный ключ не заменяется.* Ключ «группа · — · — · 0,3 кг» с
    нераспознанной маркой собирает разные товары одной фасовки (Р-21), и его
    медиана — не цена одного товара. Ни обещать экономию от такой цены, ни
    предлагать её как замену нельзя.
    """
    return key is not None and key.kind == "unit" and not key.blended


def from_history(history, asof=None, months=PRICE_WINDOW_MONTHS,
                 min_observations=MIN_OBSERVATIONS, mode=Mode.REPEATABLE):
    """Свои покупки → каталог предложений.

    Режим по умолчанию — повторяемая среда: это собственная история, сделка в
    ней не разовая, исполнение уже состоялось (SPEC §8.10).
    """
    end = asof or (history.span()[1] or "")[:10]
    if not end:
        return Catalog(offers={}, window=(None, None), source=history.source)
    start = (date.fromisoformat(end) - timedelta(days=int(months * 30.44))).isoformat()

    prices = defaultdict(list)
    stamps = defaultdict(list)
    for e in history.events:
        if e.unit_price is None or not substitutable(e.key):
            continue
        if not (start <= e.ts[:10] <= end):
            continue
        prices[e.key].append(e.unit_price)
        stamps[e.key].append(e.ts)

    offers = {}
    for key, values in prices.items():
        if len(values) < min_observations:
            continue
        offers[key] = Offer(
            key=key, unit_price=statistics.median(values),
            observations=len(values), mode=mode,
            first_ts=min(stamps[key]), last_ts=max(stamps[key]))
    return Catalog(offers=offers, window=(start, end), source=history.source)
