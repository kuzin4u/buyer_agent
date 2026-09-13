"""Экспорт профиля наружу (SPEC §9).

Схема заморожена в С1 намеренно: это единственный законный мост к агенту
покупателя Системы, у которого есть каталог и мандат, но нет никакого
представления о человеке. Её форма ограничивает то, как строится профиль, и
вывести её последней значило бы переделывать profile.

Экспортёр написан в С7 и схему не трогает: если экспорту чего-то не хватает,
это повод обсудить схему, а не дописать поле молча. Проверка `validate`
вызывается самим `build`, поэтому несоответствие видно в момент сборки, а не у
получателя.

**Наружу уходит профиль, а не история.** Адресов, дат покупок, состава чеков и
непродуктовых трат в экспорте нет и быть не должно: принимающей стороне нужно
знать, ЧТО человек берёт и почём, а не где он был во вторник.
"""

import statistics
from collections import defaultdict
from datetime import datetime, timezone

SCHEMA_VERSION = "1.0"

#: Обязательные ключи верхнего уровня и их смысл.
SCHEMA = {
    "schema_version": "версия схемы экспорта",
    "generated_at": "когда собран, ISO",
    "horizon": "режим, из которого выведены ориентиры",
    "basket": "постоянная корзина: список групп с частотой покупки",
    "frequencies": "частота покупки каждой группы, покупок в месяц",
    "price_anchors": "ценовые ориентиры по SKU: медиана ₽/базовую единицу",
}

#: Что в экспорт НЕ попадает: адреса, даты отдельных покупок, состав чеков,
#: непродуктовые траты. Наружу уходит профиль, а не история.
EXCLUDED = ("addresses", "receipt_dates", "receipt_lines", "non_food")


#: Ценовой ориентир строится только там, где медиана что-то значит (Р-2), и
#: только внутри одного товара (§4). Смешанный ключ наружу не уходит: внутри
#: него разные товары, и «ориентир» по нему — не ориентир (Р-21).
MIN_OBSERVATIONS = 3


def build(profile, history, horizon=None, asof=None):
    """Профиль + история → один JSON по замороженной схеме.

    `horizon` — режим, из которого выведены ориентиры (SPEC §8.10). Он не
    украшение: получатель должен знать, чему верить. Ориентиры собственной
    истории — повторяемая среда, и подписать их иначе нельзя.
    """
    from .matching import Horizon, OWN_HISTORY_MODE

    horizon = horizon or Horizon.of(OWN_HISTORY_MODE.value)
    generated = asof or datetime.now(timezone.utc).isoformat(timespec="seconds")

    basket = []
    frequencies = {}
    for stat in profile.staple_stats():
        basket.append({
            "group": stat.group,
            "dept": stat.dept,
            "per_month": round(stat.per_month, 3),
            "typical": stat.typical_key.label if stat.typical_key else None,
        })
        frequencies[stat.group] = round(stat.per_month, 3)

    prices = defaultdict(list)
    for event in history.events:
        if event.unit_price is None or event.key is None:
            continue
        if event.key.blended:          # внутри ключа разные товары (Р-21)
            continue
        prices[event.key].append(event.unit_price)

    anchors = []
    for key, values in prices.items():
        if len(values) < MIN_OBSERVATIONS:
            continue
        anchors.append({
            "group": key.group,
            "label": key.label,
            "unit": key.unit,
            "median": round(statistics.median(values), 2),
            "observations": len(values),
        })
    anchors.sort(key=lambda a: (a["group"], a["label"]))

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "horizon": horizon.mode.value,
        "basket": basket,
        "frequencies": frequencies,
        "price_anchors": anchors,
    }
    problems = validate(payload)
    if problems:
        # Схема заморожена: несоответствие — дефект экспортёра, а не повод
        # отдать «почти правильный» JSON наружу.
        raise ValueError("экспорт не соответствует схеме: " + "; ".join(problems))
    return payload


def validate(payload):
    """Проверка формы экспорта. Возвращает список проблем; пустой — годен."""
    problems = []
    for key in SCHEMA:
        if key not in payload:
            problems.append(f"нет обязательного ключа: {key}")
    if payload.get("schema_version") != SCHEMA_VERSION:
        problems.append(
            f"версия схемы {payload.get('schema_version')!r}, ожидалась {SCHEMA_VERSION!r}")
    for key in EXCLUDED:
        if key in payload:
            problems.append(f"в экспорт попало лишнее: {key}")
    for key in ("basket", "price_anchors"):
        if key in payload and not isinstance(payload[key], (list, dict)):
            problems.append(f"{key}: ожидался список или словарь")
    return problems
