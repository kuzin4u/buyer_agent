"""Мера полезности пополнения словарей: от бренда до разведённой строки.

Главный вопрос пользователя — «варианты моей корзины в разных магазинах», и он
упирается не в алгоритм, а в покрытие. На текущем датасете сравнимых между
магазинами товаров 25, а развести по магазинам удаётся 3 строки корзины из 8.
Остальное сравнить не с чем: у позиции не распознана марка, и ключ «группа · — ·
— · 0,3 кг» собирает разные товары, цену которых сравнивать нельзя (Р-21).

Отсюда цепочка, и она вся здесь:

    распознана группа → определена фасовка → распознан бренд
        → товар сравним между магазинами → строка корзины разводится

Пополнение словаря брендов — не косметика и не диагностика для разработчика, а
прямое продолжение главной функции. Поэтому после каждого пополнения
показывается не «покрытие выросло на 0,3%», а что стало со сравнимыми товарами и
с долей разведённой корзины.

Но цепочка имеет потолок, и он не в словарях. Маршрут 8.6 берёт у группы ОДИН
типичный товар, поэтому правило бренда попадает в «сравнимые товары» сразу, а в
«разведённые строки» — только если размеченный товар у этой группы типичный. На
текущем датасете у 6 недельных строк из 8 в группе есть сравнимый товар, а
разводится 3: разница — не непокрытие, а устройство маршрута (П-7). Мера
печатает потолок рядом с достигнутым, иначе пополнение словарей выглядит
средством от того, что им не лечится.

Модуль живёт выше слоёв: ему нужен и прогон адаптера (покрытие, фасовка, бренд),
и история с профилем (корзина, магазины). Это не нарушение шва §8.10, а его
следствие — мера сквозная по построению, и класть её внутрь любого одного слоя
значило бы дать этому слою знать про соседний.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .adapters.receipts_fns.diagnostics import coverage
from .profile import compare, for_period, smart_basket
from .profile.venues import MIN_OBSERVATIONS, REGULAR_SHARE_OF_TYPICAL

#: Период корзины, на котором меряется разведённость. Неделя, потому что это
#: обычный поход за продуктами; на месячной корзине строк больше, и доля
#: получается выше — сравнивать между собой можно только один и тот же период.
PERIOD = "week"


@dataclass(frozen=True)
class Reach:
    """Сколько агент может ответить на главный вопрос — в звеньях цепочки."""

    # --- звено 1-3: что адаптер понял про позиции ---
    analysable: int
    grouped: int              # отнесено к товарной группе
    packed: int               # фасовка или вес определены
    branded: int              # бренд распознан
    unit_items: int           # штучных позиций с фасовкой — база для бренда

    # --- звено 4: что сравнимо между магазинами ---
    #: Товары, у которых наблюдений хватает на сравнение: не меньше трёх покупок
    #: в каждом из не менее чем двух магазинов (Р-2).
    comparable_total: int
    #: Из них те, которые 8.6 действительно показывает. Остальные отсеяны как
    #: смешанные: ключ «группа · — · — · 0,3 кг» с нераспознанной маркой собирает
    #: разные товары, и сравнивать их цены нельзя (Р-21).
    comparable_shown: int


    # --- звено 5: ответ на главный вопрос ---
    basket_lines: int         # строк в недельной корзине
    split_lines: int          # из них разведено по магазинам
    saving: float             # ₽ против покупки всего в одном месте
    saving_share: float
    best_single: str = None
    #: Строки корзины, у которых в группе есть хоть один сравнимый товар. Выше
    #: этого числа разведённость не поднимется никаким пополнением словарей:
    #: маршрут 8.6 берёт у группы ОДИН типичный товар, и если сравним другой —
    #: строка всё равно не разводится. Печатать потолок обязательно, иначе
    #: пополнение словарей выглядит средством от того, что им не лечится (П-7).
    ceiling_lines: int = 0
    #: Строки, сравнённые по другой регулярной марке той же группы (Р-25).
    substituted_lines: int = 0

    @property
    def grouped_share(self):
        return self.grouped / self.analysable if self.analysable else 0.0

    @property
    def packed_share(self):
        return self.packed / self.analysable if self.analysable else 0.0

    @property
    def branded_share(self):
        return self.branded / self.unit_items if self.unit_items else 0.0

    @property
    def comparable(self):
        """Сравнимых товаров в ответе 8.6 — то самое число «25»."""
        return self.comparable_shown

    @property
    def blended_pools(self):
        """Смешанные ключи, набравшие наблюдений на сравнение.

        Соблазнительно назвать их «товарами, которым не хватает только марки», и
        это было бы неправдой. Внутри такого ключа лежат РАЗНЫЕ товары: в
        «— · —% · 0,1 кг» попали и шоколад из Перу, и белёвская пастила, и жгучий
        перец — их сравнимость создана тем, что они сложены в один пул. Правило
        бренда не разблокирует пул, а расщепляет его, и осколки чаще всего падают
        ниже порога в три наблюдения (Р-2).

        Поэтому число полезно как указатель объёма работы, но не как обещание
        прибавки, и падать оно может одновременно с ростом ответа (Р-18).
        """
        return self.comparable_total - self.comparable_shown

    @property
    def ceiling_share(self):
        return self.ceiling_lines / self.basket_lines if self.basket_lines else 0.0

    @property
    def split_share(self):
        """Доля корзины, которую вообще удаётся развести по магазинам."""
        return self.split_lines / self.basket_lines if self.basket_lines else 0.0


def measure(run, history, profile, period=PERIOD, settings=None):
    """Прогон + история + профиль → одна мера по всей цепочке."""
    cov = coverage(run)
    basket = for_period(profile, period, settings)
    route = smart_basket(history, basket)
    shown = compare(history)
    groups_with_comparable = {c.key.group for c in shown}
    # Сравнимость берётся у самой функции 8.6, а не считается заново: панель,
    # которая показывает «сравнимых 58», пока страница показывает 25, хуже, чем
    # отсутствие панели — она выглядит так же убедительно и при этом врёт.
    return Reach(
        analysable=cov.analysable,
        grouped=cov.matched_all,
        packed=cov.with_pack,
        branded=cov.brand_hit,
        unit_items=cov.unit_items,
        comparable_total=len(compare(history, include_blended=True)),
        comparable_shown=len(shown),
        ceiling_lines=sum(1 for line in basket.lines
                          if line.group in groups_with_comparable),
        substituted_lines=len(route.substituted),
        # Знаменатель — все строки корзины, а не только те, что дошли до
        # сравнения: у маршрута три исхода (развели, сравнить нечем, нет у
        # базовой площадки), и сложить надо все.
        basket_lines=route.considered,
        split_lines=len(route.lines),
        saving=route.saving_abs,
        saving_share=route.saving_pct,
        best_single=route.best_single,
    )


@dataclass(frozen=True)
class Step:
    """Одно звено цепочки: что было, что стало, что это дало."""

    name: str
    unit: str                 # «позиций», «товаров», «строк», «₽»
    before: float
    after: float
    before_share: float = None
    after_share: float = None
    #: Ради этого звена всё и делается. Печатать его особо — не оформление:
    #: рост распознанных позиций без роста разведённых строк означает, что
    #: работа не приблизила ответ на главный вопрос.
    decisive: bool = False

    @property
    def delta(self):
        return self.after - self.before

    @property
    def delta_share(self):
        if self.before_share is None or self.after_share is None:
            return None
        return self.after_share - self.before_share

    @property
    def moved(self):
        return abs(self.delta) > 1e-9


def chain(before, after):
    """Две меры → цепочка звеньев, от бренда к разведённой строке.

    Порядок звеньев — порядок причинности, а не важности: пользователь видит,
    на каком звене его правило подействовало, а на каком нет. Бывает и так, что
    бренд распознан, а сравнимых товаров не прибавилось: новая марка встречалась
    в одном магазине или реже трёх раз (Р-2), и тогда звено честно стоит на
    месте.
    """
    return (
        Step("Распознана группа", "позиций", before.grouped, after.grouped,
             before.grouped_share, after.grouped_share),
        Step("Определена фасовка", "позиций", before.packed, after.packed,
             before.packed_share, after.packed_share),
        Step("Распознан бренд", "штучных позиций", before.branded, after.branded,
             before.branded_share, after.branded_share),
        Step("Смешанных пулов", "ключей",
             before.blended_pools, after.blended_pools),
        Step("Сравнимо между магазинами", "товаров",
             before.comparable, after.comparable, decisive=True),
        Step("Разводится по магазинам", "строк корзины",
             before.split_lines, after.split_lines,
             before.split_share, after.split_share, decisive=True),
        Step("Потолок разведённости", "строк корзины",
             before.ceiling_lines, after.ceiling_lines,
             before.ceiling_share, after.ceiling_share),
        Step("Экономия корзины", "₽", before.saving, after.saving,
             before.saving_share, after.saving_share, decisive=True),
    )


def useful(before, after):
    """Приблизило ли пополнение ответ на главный вопрос.

    Мера намеренно строгая: полезно то, что двигает последнее звено. Рост
    покрытия сам по себе полезным не считается — он средство, а не цель.
    """
    return (after.split_lines > before.split_lines
            or after.comparable_shown > before.comparable_shown)


# --- потолок как очередь работ (П-7 закрыт решением Р-25) ---

#: Чего не хватает типичному товару строки, чтобы его можно было сравнить.
#: `actionable` значит «лечится пополнением словаря прямо с панели»; остальное —
#: не задание, а факт о покупках, и выдавать его за задание нельзя.
BLOCKERS = {
    "no_key": ("у группы нет типичного товара: фасовка не определена ни у одной "
               "позиции", True),
    "blended": ("марка не распознана: ключ собирает разные товары, и сравнивать "
                "их цены нельзя", True),
    "one_venue": ("вы берёте это от трёх раз только в одном магазине — сравнить "
                  "не с чем", False),
    "too_few": ("ни в одном магазине не набралось трёх покупок", False),
    "outside_baseline": ("сравнить можно, но у базовой одиночной площадки этого "
                         "товара нет", False),
}


@dataclass(frozen=True)
class Task:
    """Строка корзины, которая не разводится, и чего ей не хватает."""

    group: str
    typical: object           # типичный товар группы; бывает None
    blocker: str              # ключ из BLOCKERS
    amount: float             # сколько эта строка стоит в корзине
    names: tuple = ()         # названия внутри ключа — с чем работать
    venue: str = None         # магазин, если дело в нём
    alternative: object = None   # сравнимая марка группы, которую не подставили
    alternative_share: float = None
    #: Почему не подставили: "share" — берётся слишком редко, чтобы считать её
    #: вашей второй маркой; "unit" — продаётся в другой единице, и ₽/кг с ₽/л
    #: несопоставимы (SPEC §5). Причина должна называться точно: «слишком редко»
    #: про марку, которую берут в 70% случаев, — это ложь в ответе.
    alternative_blocker: str = None

    @property
    def reason(self):
        return BLOCKERS[self.blocker][0]

    @property
    def actionable(self):
        return BLOCKERS[self.blocker][1]


def tasks(run, history, profile, period=PERIOD, settings=None,
          min_observations=MIN_OBSERVATIONS,
          min_share=REGULAR_SHARE_OF_TYPICAL):
    """Неразведённые строки корзины → что с каждой можно сделать.

    Это и есть бывший потолок, переведённый из ограничения в задание. Половина
    зазора лечится словарём (марка не распознана — правило бренда расщепит ключ),
    половина не лечится ничем: вы берёте этот товар только в одном магазине, и
    сравнивать его цену попросту не с чем. Выдавать второе за задание нельзя —
    человек будет раз за разом пытаться исправить то, что исправлению не
    подлежит.
    """
    basket = for_period(profile, period, settings)
    route = smart_basket(history, basket, min_observations=min_observations,
                         min_share=min_share)
    split = {line.instead_of or line.key for line in route.lines}
    outside = {line.typical_key for line in route.outside_baseline}
    comparable = {c.key for c in compare(history)}

    by_key_venue = defaultdict(Counter)
    for event in history.events:
        if event.key is not None and event.venue:
            by_key_venue[event.key][event.venue] += 1
    names = defaultdict(Counter)
    for item in run.items:
        if item.key is not None:
            names[item.key][item.name] += 1
    frequencies = defaultdict(Counter)
    for event in history.events:
        if event.key is not None:
            frequencies[event.key.group][event.key] += 1

    out = []
    for line in basket.lines:
        key = line.typical_key
        if key in split:
            continue
        # Сравнимая марка группы, которую подставить не удалось: показать её
        # полезно — видно, что потолок упирается не в словарь, а в привычку или
        # в несопоставимые единицы.
        alternative, share, why = None, None, None
        ranked = frequencies.get(key.group if key else None) or Counter()
        top = ranked.most_common(1)[0][1] if ranked else 0
        for candidate, count in ranked.most_common():
            if candidate == key or candidate not in comparable or not top:
                continue
            alternative, share = candidate, count / top
            if key is not None and candidate.unit != key.unit:
                why = "unit"
            elif share < min_share:
                why = "share"
            break

        if key is None:
            blocker, venue = "no_key", None
        elif key in outside:
            blocker, venue = "outside_baseline", None
        elif key.blended:
            blocker, venue = "blended", None
        else:
            enough = [v for v, n in by_key_venue[key].items()
                      if n >= min_observations]
            if len(enough) == 1:
                blocker, venue = "one_venue", enough[0]
            else:
                blocker, venue = "too_few", None

        out.append(Task(
            group=line.group, typical=key, blocker=blocker, amount=line.amount,
            names=tuple(n for n, _c in names[key].most_common(4)) if key else (),
            venue=venue, alternative=alternative, alternative_share=share,
            alternative_blocker=why))

    # Дорогие строки раньше: пользу от работы мерят рублями корзины. Задания
    # раньше фактов — по ним есть что делать.
    out.sort(key=lambda t: (not t.actionable, -t.amount))
    return out
