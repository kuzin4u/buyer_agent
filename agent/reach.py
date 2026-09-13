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

from dataclasses import dataclass

from .adapters.receipts_fns.diagnostics import coverage
from .profile import compare, for_period, smart_basket

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
        basket_lines=len(route.lines) + len(route.skipped),
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
