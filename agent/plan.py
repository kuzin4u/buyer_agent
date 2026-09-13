"""Сценарий «продукты на неделю» целиком: варианты, выбор, список по магазинам.

Отдельные функции 8.2, 8.3 и 8.6 отвечают на разные вопросы, и человек ходил
между тремя страницами, сводя их в голове. Здесь они сведены в один поток:
корзина → чем различаются варианты → выбор → список, разложенный по магазинам.

**Варианты не пересчитываются заново.** Всё, что здесь есть, посчитано ядром
раньше: корзина — 8.2, маршрут и лучшая одиночная площадка — 8.6, замены и
выбросы — 8.3. Этот модуль их только совмещает и следит, чтобы совмещение было
честным. Своей арифметики у него ровно столько, сколько нужно, чтобы сложить
рубли внутри одного и того же основания.

**Главное ограничение — не алгоритм, а покрытие, и оно печатается первым.**
Магазин известен не у всех строк: цену можно сравнить только там, где у товара
есть не меньше трёх наблюдений у каждой из двух площадок (Р-2), и на текущем
датасете это 3 строки недельной корзины из 8. Остальные 5 едут в основной
магазин не потому, что там дешевле, а потому что сравнить не с чем. Разница
между «выбрали лучший магазин» и «не из чего было выбирать» — это и есть разница
между советом и его видимостью, поэтому у каждой неразведённой строки стоит
причина, а доля разведённых печатается рядом с любой экономией (Р-2, Р-21).

**Два основания, и они не складываются.** Строка с известным магазином считается
в сегодняшних ценах — медиана в окне сравнения (Р-27). Строка без магазина
известна только обычной тратой: медианой того, сколько на неё уходит, по всей
истории. Это разные деньги: на датасете одни и те же три строки стоят 1 040 ₽ по
обычной трате и 1 397 ₽ в ценах окна, и разница — не ошибка, а инфляция, которую
Р-27 уже поймал в сравнении площадок. Поэтому общий итог варианта здесь НЕ
считается: сложить их значило бы получить правдоподобное число, которого нет
(Р-21). Печатаются оба, каждое со своим знаменателем (Р-13).

**Цена выбора — это не сумма.** Вариант описывается тем, чем за него платят:
лишними заездами, переплатой против развоза, незакрытыми потребностями. Сумма
одна и та же во всех вариантах у пяти строк из восьми, и различать варианты по
ней значило бы делать вид, что выбор есть там, где его нет.
"""

from dataclasses import dataclass

from .history import PRICE_WINDOW_MONTHS
from .matching import budget as B
from .profile import for_period, smart_basket

#: Варианты в порядке показа. Порядок не произволен: сначала то, что не требует
#: ни лишних поездок, ни отказа от покупок, потом то, что требует поездок, потом
#: то, что требует отказа. Дальше от привычного — ниже.
VARIANTS = ("single", "split", "budget")

#: Почему у строки не выбран магазин. Причина, а не признак: «марку не
#: распознали» пользователь может исправить, а «берёте только в одном магазине»
#: — нет, и гонять его за этим нельзя (Р-25).
NO_TYPICAL = "нет типичного товара: группа закрывается разным"
NO_UNIT_PRICE = "нет цены за единицу — не из чего считать количество"
BLENDED = "марка не распознана: под одним ключом разные товары"
ONE_VENUE = "цена известна не у двух магазинов сразу"
NOT_AT_SINGLE = "сравнимо, но у одиночного магазина этой строки нет"


@dataclass(frozen=True)
class PlanLine:
    """Строка списка: что берём, где и почём.

    `venue_known` отделяет выбор от умолчания. Магазин стоит у каждой строки —
    иначе список неполон, — но у пяти строк из восьми он проставлен за
    неимением сравнения, и выдать это за выбор значит соврать.
    """

    group: str
    dept: str
    label: str
    qty: int
    amount: float             # обычная трата на эту строку, ₽ (основание 8.2)
    venue: str = None
    venue_known: bool = False
    reason: str = None        # почему магазин не выбран
    cost: float = None        # цена в окне сравнения, ₽ (основание 8.6)
    unit_price: float = None
    units: float = None
    unit: str = None
    saving: float = 0.0       # против той же строки у одиночного магазина
    instead_of: object = None
    share_of_typical: float = None
    months_old: float = None
    stale: bool = False
    swap: object = None       # чем заменили (8.3)

    @property
    def priced(self):
        """Цену этой строки можно назвать по магазину, а не по своей истории."""
        return self.cost is not None

    @property
    def substituted(self):
        return self.instead_of is not None


@dataclass(frozen=True)
class Variant:
    """Один способ закрыть ту же неделю — и чем за него платят."""

    id: str
    lines: tuple              # PlanLine, то, что берём
    dropped: tuple = ()       # PlanLine, от чего отказались (8.3)
    #: Строки, посчитанные в ценах окна. Подмножество lines, вынесено ради
    #: знаменателя: доля разведённого считается от ВСЕХ строк корзины (Р-13).
    priced_total: float = 0.0
    usual_total: float = 0.0  # обычная трата строк, у которых цены по магазину нет
    baseline_total: float = 0.0   # сравнимая часть у лучшего одиночного магазина
    #: Чем платят за вариант. Ни одно из этих чисел не является суммой корзины.
    overpay: float = 0.0      # переплата против развоза, ₽
    saving: float = 0.0       # экономия против одиночного магазина, ₽
    dropped_amount: float = 0.0   # обычная трата того, от чего отказались
    swaps: tuple = ()
    fits: bool = True
    budget: float = None
    slack: float = None

    @property
    def venues(self):
        """Магазины, в которые придётся заехать. Пустой список — некуда."""
        return tuple(sorted({l.venue for l in self.lines if l.venue}))

    @property
    def stops(self):
        return len(self.venues)

    @property
    def saving_share(self):
        """Экономия как доля сравнимой части — от неё, а не от корзины (Р-13).

        Знаменатель именно сравнимая часть: доля от всей корзины была бы честнее
        на вид и бессмысленнее по существу — экономия к строкам, которых она не
        касается, отношения не имеет.
        """
        return self.saving / self.baseline_total if self.baseline_total else 0.0

    @property
    def overpay_share(self):
        return self.overpay / self.baseline_total if self.baseline_total else 0.0

    @property
    def priced_lines(self):
        return tuple(l for l in self.lines if l.priced)

    @property
    def unpriced_lines(self):
        return tuple(l for l in self.lines if not l.priced)

    @property
    def chosen_venue_lines(self):
        """Строки, магазин которых выбран сравнением, а не назначен умолчанием."""
        return tuple(l for l in self.lines if l.venue_known)

    @property
    def unmet(self):
        """Потребности, оставшиеся незакрытыми. Отказ — тоже цена (Р-22)."""
        return self.dropped

    @property
    def substituted(self):
        return tuple(l for l in self.lines if l.substituted)

    @property
    def stale(self):
        return tuple(l for l in self.lines if l.stale)

    def by_venue(self):
        """→ [(магазин, [строки])], магазин с наибольшим числом строк первым.

        Готовый список: человек идёт в магазин и читает свой кусок. Строки без
        выбранного магазина лежат в том же магазине, куда их отправили, и
        отличимы по `venue_known` — смешать их со сравнёнными нельзя.
        """
        out = {}
        for line in self.lines:
            out.setdefault(line.venue, []).append(line)
        for lines in out.values():
            lines.sort(key=lambda l: (not l.venue_known, -l.amount))
        return sorted(out.items(),
                      key=lambda kv: (-len(kv[1]), kv[0] or ""))


@dataclass(frozen=True)
class Plan:
    """Неделя целиком: корзина, варианты, выбранный вариант и покрытие."""

    period: str
    basket: object
    variants: tuple
    chosen: str
    #: Что попросили показать. Отличается от `chosen`, когда запрошенного
    #: варианта нет: «под сумму» без суммы не строится, и подменить его молча
    #: нельзя — подстановка неотличима от правды (Р-23).
    requested: str = None
    main_venue: str = None
    best_single: str = None
    window_months: int = PRICE_WINDOW_MONTHS
    #: Строки корзины, у которых магазин не выбран, с причинами. Один и тот же
    #: набор во всех вариантах — поэтому лежит у плана, а не у варианта.
    unpriced_reasons: tuple = ()

    @property
    def honoured(self):
        """Показывается ли то, что просили."""
        return self.requested is None or self.requested == self.chosen

    @property
    def variant(self):
        """Выбранный вариант. Выбор всегда явный: умолчание тоже есть в id."""
        return next(v for v in self.variants if v.id == self.chosen)

    def get(self, id):
        return next((v for v in self.variants if v.id == id), None)

    @property
    def considered(self):
        """Знаменатель для всех долей плана — строки корзины (Р-13)."""
        return len(self.basket.lines)

    @property
    def priced(self):
        """Сколько строк удалось развести. Одинаково во всех вариантах: цену
        знает не вариант, а история."""
        return len(self.get("split").priced_lines)

    @property
    def unpriced(self):
        """Строк, у которых магазин проставлен за неимением сравнения."""
        return self.considered - self.priced

    @property
    def covered(self):
        return self.priced / self.considered if self.considered else 0.0

    @property
    def usual_total(self):
        """Обычная трата на всю корзину — основание 8.2, не цена в магазине."""
        return self.basket.total

    @property
    def extra_stops(self):
        """Во сколько лишних заездов обходится развоз."""
        return self.get("split").stops - self.get("single").stops

    @property
    def usual_of_priced(self):
        """Обычная трата по тем же строкам, которым нашлась цена в магазине.

        Нужна ровно затем, чтобы два основания можно было сопоставить на одних и
        тех же строках, а не на глаз.
        """
        return sum(l.amount for l in self.get("split").priced_lines)

    @property
    def price_gap(self):
        """На сколько сегодняшняя цена сравнимой части выше обычной траты на неё.

        Это не ошибка счёта и не наценка магазина, а разница лет: обычная трата
        — медиана по всей истории, цена — медиана в окне сравнения (Р-27).
        На датасете 1 465 ₽ против 1 040 ₽ по одним и тем же трём строкам.

        Печатать обязательно, и вот почему. Бюджетный вариант проверяет
        «уложились» по обычной трате, то есть в деньгах прошлого, а рядом на том
        же экране стоит сегодняшняя цена. Человек, увидевший «уложились в 2 000 ₽»
        и «1 465 ₽ за три строки из восьми», имеет право знать, что это два
        разных рубля. Пока 8.2 считает медиану по всей истории, разрыв
        принципиально неустраним показом (П-9).
        """
        return self.get("single").priced_total - self.usual_of_priced


def _reason(line, route):
    """Почему у этой строки не выбран магазин.

    Причина считается один раз и до ветвления (Р-14): она определяется самой
    строкой и историей, а не тем, в какой вариант строка попала.
    """
    key = line.typical_key
    if key is None:
        return NO_TYPICAL
    if not line.unit_price:
        return NO_UNIT_PRICE
    if getattr(key, "blended", False):
        return BLENDED
    if any(l is line for l in route.outside_baseline):
        return NOT_AT_SINGLE
    return ONE_VENUE


def _line(basket_line, venue, venue_known=False, reason=None, route_line=None,
          cost=None, swap=None):
    """Строка корзины → строка списка. Числа переносятся, а не пересчитываются."""
    extra = {}
    if route_line is not None:
        extra = {"unit_price": route_line.unit_price, "units": route_line.units,
                 "unit": route_line.unit, "instead_of": route_line.instead_of,
                 "share_of_typical": route_line.share_of_typical,
                 "months_old": route_line.months_old, "stale": route_line.stale}
    return PlanLine(
        group=basket_line.group, dept=basket_line.dept,
        label=(swap.to_key.label if swap else basket_line.label),
        qty=basket_line.qty, amount=basket_line.amount,
        venue=venue, venue_known=venue_known, reason=reason,
        cost=cost, saving=(route_line.saving if route_line else 0.0),
        swap=swap, **extra)


def build(history, profile, catalog, settings=None, period="week", budget=None,
          chosen=None):
    """Неделя целиком: корзина, три варианта, выбранный и покрытие.

    `budget` — сумма для третьего варианта. Не задан: берётся из настроек
    пользователя (§3.3), а если и там пусто, вариант «под сумму» не строится
    вовсе. Подставлять сумму молча нельзя: «под 2000» и «под 3000» дают разные
    ответы, и непонятое обязано называть себя непонятым (Р-23).
    """
    basket = for_period(profile, period, settings)
    route = smart_basket(history, basket)
    main_venue = (getattr(settings, "main_venue", None) or profile.main_venue)
    single_venue = route.best_single or main_venue

    # Строка корзины → строка маршрута, если её удалось развести. Ключ
    # сравнивается по значению, а не по тождеству: `compare` строит свои
    # объекты ключей, и по `is` не совпал бы ни один. У подставленной марки
    # (Р-25) исходный ключ лежит в `instead_of` — берётся он, иначе строка
    # потеряется именно там, где подстановка и произошла.
    by_line = {}
    for index, basket_line in enumerate(basket.lines):
        for route_line in route.lines:
            target = route_line.instead_of or route_line.key
            if basket_line.typical_key == target:
                by_line[index] = route_line
                break

    why = {i: _reason(basket.lines[i], route)
           for i in range(len(basket.lines)) if i not in by_line}
    reasons = tuple((basket.lines[i], why[i]) for i in sorted(why))

    def compose(venue_of, default_venue, keep=None, swaps=None):
        """Собрать вариант: каждой строке магазин и, если известна, цена.

        `default_venue` — куда едет строка, магазин которой не выбран. Он
        отличается по вариантам: «в одном месте» везёт туда же, где считалась
        сравнимая часть, «врозь» — в основной магазин.
        """
        lines = []
        for index, basket_line in enumerate(basket.lines):
            if keep is not None and index not in keep:
                continue
            route_line = by_line.get(index)
            swap = (swaps or {}).get(index)
            if route_line is None:
                lines.append(_line(basket_line, venue=default_venue,
                                   venue_known=False, reason=why[index],
                                   swap=swap))
            else:
                venue, cost = venue_of(route_line)
                lines.append(_line(basket_line, venue=venue, venue_known=True,
                                   route_line=route_line, cost=cost, swap=swap))
        return lines

    def totals(lines):
        priced = sum(l.cost for l in lines if l.priced)
        usual = sum(l.amount for l in lines if not l.priced)
        return priced, usual

    # --- вариант 1: всё в одном месте ---
    # Цена сравнимой части — у лучшей одиночной площадки, ровно та, против
    # которой 8.6 считает экономию. Неразведённые строки едут туда же: в одном
    # месте — значит в одном.
    single_lines = compose(lambda r: (single_venue, r.baseline_cost),
                           default_venue=single_venue)
    priced, usual = totals(single_lines)
    single = Variant(id="single", lines=tuple(single_lines), priced_total=priced,
                     usual_total=usual, baseline_total=route.baseline_total,
                     overpay=route.saving_abs)

    # --- вариант 2: врозь, по самой дешёвой площадке на строку ---
    split_lines = compose(lambda r: (r.venue, r.cost),
                          default_venue=main_venue)
    priced, usual = totals(split_lines)
    split = Variant(id="split", lines=tuple(split_lines), priced_total=priced,
                    usual_total=usual, baseline_total=route.baseline_total,
                    saving=route.saving_abs)

    variants = [single, split]

    # --- вариант 3: под заданную сумму ---
    amount = budget if budget is not None else getattr(settings, "budget", None)
    if amount:
        fit = B.fit(profile, catalog, amount, period=period, settings=settings)
        # Соответствие строк 8.3 строкам корзины — по группе: `fit` собирает
        # корзину сам, и его строки — другие объекты с теми же значениями.
        # Группа для этого годится и годится однозначно: строка корзины на
        # группу ровно одна, это и есть уровень атома 8.2 (SPEC §4).
        index_of = {l.group: i for i, l in enumerate(basket.lines)}
        keep, swaps = set(), {}
        for fit_line in fit.lines:
            index = index_of.get(fit_line.group)
            if index is None:
                continue
            keep.add(index)
            if fit_line.swap:
                swaps[index] = fit_line.swap
        dropped = tuple(_line(l, venue=None) for l in fit.dropped)
        fit_lines = compose(lambda r: (single_venue, r.baseline_cost),
                            default_venue=single_venue, keep=keep, swaps=swaps)
        priced, usual = totals(fit_lines)
        variants.append(Variant(
            id="budget", lines=tuple(fit_lines), dropped=dropped,
            priced_total=priced, usual_total=usual,
            baseline_total=route.baseline_total,
            dropped_amount=sum(l.amount for l in dropped),
            swaps=fit.swaps, fits=fit.fits, budget=amount, slack=fit.slack))

    ids = [v.id for v in variants]
    return Plan(period=period, basket=basket, variants=tuple(variants),
                chosen=(chosen if chosen in ids else ids[0]), requested=chosen,
                main_venue=main_venue, best_single=route.best_single,
                unpriced_reasons=reasons)
