"""8.3 Корзина под бюджет (SPEC §8.3).

«Уложись в N ₽». От 8.2 отличается не параметром, а вопросом. 8.2 описывает:
вот что обычно набирается. 8.3 предписывает: вот как уложиться, — и поэтому у
неё есть рычаг, которого у 8.2 нет.

Рычагов ровно два, и порядок между ними не произволен:

1. **Замена дорогой позиции на дешёвую внутри той же группы** — главный
   (§8.3). Потребность остаётся закрытой: молоко в корзине есть, просто другой
   марки. Чем именно заменили — показывается всегда, это требование §8.3.
2. **Выбросить группу** — последний. Потребность остаётся незакрытой, поэтому
   сюда доходит только то, что не уложилось заменами, и никогда — обязательные
   позиции пользователя (§3.3).

Горизонт не параметр этой функции, хотя и стоит в сигнатуре: он выводится из
режима предложения (SPEC §8.10). Переданный извне горизонт проверяется на
соответствие каталогу и чужой отвергается — иначе агенту покупателя можно было
бы выставить продавцовский горизонт, и осталось бы одно обещание так не делать.
"""

from dataclasses import dataclass

from . import Horizon, Mode, check_horizon
from .catalog import substitutable
from ..profile.basket import PERIODS, for_period

#: Насколько дешевле должна быть замена, чтобы её стоило предлагать САМУ ПО
#: СЕБЕ. Ниже этого разница лежит внутри шума медианы по трём наблюдениям, а
#: привычка меняется по-настоящему: на датасете порог отсекает «Савушкин 9% →
#: Савушкин 5%» ради 11 ₽ и оставляет «Боржоми → Арза» (−61%).
#:
#: Но порог не запрет, а очередь. Если без мелкой замены в бюджет не уложиться,
#: она применяется: выброшенная группа — это незакрытая потребность, и она хуже
#: любой замены марки внутри той же группы. Иначе получался бы прямой абсурд —
#: выбросить сметану целиком, чтобы не менять творог на 11 ₽ дешевле.
MIN_GAIN_SHARE = 0.10


@dataclass(frozen=True)
class Swap:
    """Чем заменили — и сколько это дало."""

    from_key: object
    to_key: object
    from_price: float         # ₽ за базовую единицу, по каталогу
    to_price: float
    units: float              # сколько базовых единиц берётся
    unit: str
    min_gain: float = MIN_GAIN_SHARE

    @property
    def saving(self):
        return (self.from_price - self.to_price) * self.units

    @property
    def gain_share(self):
        """Насколько дешевле за единицу. Безразмерно, сравнимо между группами."""
        return (self.from_price - self.to_price) / self.from_price if self.from_price else 0.0

    @property
    def worthwhile(self):
        """Стоит ли эта замена сама по себе, без давления бюджета."""
        return self.gain_share >= self.min_gain

    @property
    def marginal(self):
        """Применена только потому, что иначе пришлось бы выбросить группу."""
        return not self.worthwhile


@dataclass(frozen=True)
class FitLine:
    """Строка корзины после подбора: та же потребность, возможно другой товар."""

    line: object              # исходная BasketLine
    swap: Swap = None
    blocked: str = None       # почему замены нет, если её нет

    @property
    def group(self):
        return self.line.group

    @property
    def dept(self):
        return self.line.dept

    @property
    def qty(self):
        return self.line.qty

    @property
    def reason(self):
        return self.line.reason

    @property
    def amount(self):
        """Сумма после замены. Экономия считается по одной линейке — каталожной
        цене до и после, — поэтому базовая сумма остаётся той же, что в 8.2."""
        return self.line.amount - (self.swap.saving if self.swap else 0.0)

    @property
    def label(self):
        return self.swap.to_key.label if self.swap else self.line.label

    @property
    def was(self):
        return self.line.label


@dataclass(frozen=True)
class Fit:
    """Корзина, уложенная в бюджет (или не уложенная — и тогда это видно)."""

    budget: float
    period: str
    lines: tuple              # FitLine, то, что остаётся в корзине
    dropped: tuple            # BasketLine, выброшенное
    horizon: Horizon
    total_before: float       # та же корзина без подбора
    comparable: int = 0       # строк, для которых каталог знает цену
    substitutable: int = 0    # строк, которые вообще можно было заменить

    @property
    def total(self):
        return sum(line.amount for line in self.lines)

    @property
    def considered(self):
        """Сколько строк было в исходной корзине — знаменатель для покрытия."""
        return len(self.lines) + len(self.dropped)

    @property
    def swaps(self):
        return tuple(line.swap for line in self.lines if line.swap)

    @property
    def saved_by_swaps(self):
        return sum(s.saving for s in self.swaps)

    @property
    def saved_by_drops(self):
        return sum(line.amount for line in self.dropped)

    @property
    def fits(self):
        return self.total <= self.budget

    @property
    def empty(self):
        """Бюджет закрылся пустой корзиной.

        Формально это «уложились», по существу — нет: не купить ничего дешевле
        любого набора, и ответ «уложились, запас 50 ₽» был бы издёвкой. Поэтому
        признак вынесен наружу и печатается отдельно.
        """
        return not self.lines

    @property
    def cheapest_dropped(self):
        """Самая дешёвая выброшенная строка — чем мерить недостаточный бюджет."""
        return min(self.dropped, key=lambda l: l.amount, default=None)

    @property
    def slack(self):
        """Сколько осталось до бюджета. Отрицательное — на сколько не уложились."""
        return self.budget - self.total

    @property
    def substitution_allowed(self):
        return self.horizon.allows_substitution

    def by_dept(self):
        out = {}
        for line in self.lines:
            out.setdefault(line.dept or "—", []).append(line)
        return out


def _units(line, price):
    """Сколько базовых единиц стоит за строкой корзины."""
    if not price:
        return None
    return line.amount / price


def candidates(catalog, key, min_gain=MIN_GAIN_SHARE):
    """Чем можно закрыть ту же потребность дешевле — от самого дешёвого.

    Отсев, кроме цены, один: и заменяемое, и заменяющее обязаны быть такими,
    что их вообще можно сравнивать (catalog.substitutable).
    """
    if not substitutable(key):
        return ()
    here = catalog.get(key)
    if here is None:
        return ()
    out = []
    for offer in catalog.alternatives(key):
        if offer.key == key or not substitutable(offer.key):
            continue
        gain = (here.unit_price - offer.unit_price) / here.unit_price
        if gain >= min_gain:
            out.append(offer)
    return tuple(out)


def _blocked_reason(catalog, key, horizon=None):
    """Почему эту строку заменить нельзя. Печатать обязательно: «замен нет» и
    «заменять нечем» — разные ответы, и второй пользователь может исправить,
    пополнив словарь брендов."""
    if horizon is not None and not horizon.allows_substitution:
        return "разовая среда: позиция показана, но замена привычного не предлагается"
    if key is None:
        return "нет типичного товара"
    if key.kind == "bulk":
        return "весовой товар: внутри группы это разные товары, а не марки"
    if key.blended:
        return "марка не распознана — пополните словарь брендов"
    if catalog.get(key) is None:
        return "нет цены в окне каталога"
    if not catalog.alternatives(key)[1:]:
        return "нечем заменить: другой марки в истории нет"
    return "уже самая дешёвая из известных"


def _horizon_for(catalog, horizon=None):
    """Горизонт подбора: выводится из каталога, переданный извне — проверяется.

    Это и есть шов §8.10. Горизонт стоит в сигнатуре `fit`, но взять его можно
    только там, где лежит позиция: чужой горизонт не проходит проверку, поэтому
    выставить агенту покупателя продавцовский нельзя — не «запрещено», а
    неоткуда взять.
    """
    mode = catalog.mode
    if mode is None:
        # Смешанный каталог общего горизонта не имеет. Пока подбор работает
        # корзиной целиком, единственный честный выбор — осторожный: верить
        # только цене и не предлагать замену привычного (SPEC §8.10, таблица).
        if horizon is not None:
            raise ValueError(
                "каталог смешанного режима: общего горизонта у него нет, "
                "горизонт выводится из режима каждой позиции (SPEC §8.10)")
        return Horizon.of(Mode.ONE_OFF.value)
    return (check_horizon(mode.value, horizon) if horizon is not None
            else Horizon.of(mode.value))


def fit(profile, catalog, budget, period="week", settings=None, horizon=None,
        min_gain=MIN_GAIN_SHARE):
    """Корзина, уложенная в `budget`.

    Порядок приоритета — регулярным группам (§8.3): обязательные позиции
    пользователя, затем по частоте покупки. Выбрасывается с конца этого
    порядка, то есть самое нерегулярное.
    """
    if budget is None or budget <= 0:
        raise ValueError(f"бюджет {budget!r}: ожидалось положительное число")
    if period not in PERIODS:
        raise ValueError(f"период {period!r}: ожидался один из {sorted(PERIODS)}")

    horizon = _horizon_for(catalog, horizon)
    basket = for_period(profile, period, settings)
    required = set(getattr(settings, "required_groups", ()) or ())

    def regularity(line):
        stat = profile.groups.get(line.group)
        return stat.per_month if stat else 0.0

    # Порядок приоритета. Считается один раз и до ветвления (Р-14).
    ordered = sorted(basket.lines,
                     key=lambda l: (l.group not in required, -regularity(l), l.group))
    total_before = sum(line.amount for line in basket.lines)

    # Что известно про каждую строку — тоже до ветвления: и покрытие каталога,
    # и возможная замена считаются один раз, а не внутри удачной ветки.
    comparable = can_swap = 0
    plan, blocked = {}, {}
    for idx, line in enumerate(ordered):
        key = line.typical_key
        here = catalog.price(key)
        if here is not None:
            comparable += 1
        cheaper = (candidates(catalog, key, min_gain=0.0)
                   if horizon.allows_substitution else ())
        if cheaper:
            can_swap += 1
        units = _units(line, here)
        if cheaper and units is not None:
            plan[idx] = Swap(from_key=key, to_key=cheaper[0].key, from_price=here,
                             to_price=cheaper[0].unit_price, units=units,
                             unit=cheaper[0].unit, min_gain=min_gain)
        else:
            blocked[idx] = _blocked_reason(catalog, key, horizon)

    live = list(range(len(ordered)))        # что ещё в корзине, в порядке приоритета
    applied, dropped = {}, []

    def amount(idx):
        swap = applied.get(idx)
        return ordered[idx].amount - (swap.saving if swap else 0.0)

    def total():
        return sum(amount(i) for i in live)

    # --- рычаг 1: замена дорогого на дешёвое внутри той же группы ---
    # Применяется не всё подряд, а пока не уложились: менять привычку сверх
    # необходимого незачем. Порядок — по убыванию экономии, чтобы тронуть как
    # можно меньше строк.
    for idx in sorted((i for i, s in plan.items() if s.worthwhile),
                      key=lambda i: -plan[i].saving):
        if total() <= budget:
            break
        applied[idx] = plan[idx]

    # --- рычаг 2: выбросить самое нерегулярное ---
    # Перед каждым выбросом проверяется, не закроют ли остаток мелкие замены —
    # те, что сами по себе не стоили бы предложения. Если закроют, применяются
    # они: группа остаётся в корзине, а это важнее нетронутой привычки. Если не
    # закроют, привычку трогать незачем, и выброс состоится всё равно.
    while total() > budget:
        spare = {i: plan[i] for i in live if i in plan and i not in applied}
        if spare and sum(s.saving for s in spare.values()) >= total() - budget:
            for idx in sorted(spare, key=lambda i: -spare[i].saving):
                if total() <= budget:
                    break
                applied[idx] = spare[idx]
            break
        victim = next((i for i in reversed(live)
                       if ordered[i].reason != "обязательная"), None)
        if victim is None:
            break          # осталось только обязательное — бюджет недостижим
        live.remove(victim)
        dropped.append(ordered[victim])

    lines = [FitLine(line=ordered[i], swap=applied.get(i),
                     blocked=(None if i in applied else blocked.get(i)))
             for i in live]
    lines.sort(key=lambda l: -l.amount)
    return Fit(budget=budget, period=period, lines=tuple(lines),
               dropped=tuple(dropped), horizon=horizon,
               total_before=total_before, comparable=comparable,
               substitutable=can_swap)
