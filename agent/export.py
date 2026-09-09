"""Экспорт профиля наружу (SPEC §9, спринт С7).

Схема заморожена в С1 намеренно: это единственный законный мост к агенту
покупателя Системы, и её форма ограничивает то, как строится профиль. Вывести
её последней значит переделывать profile.

Сам экспортёр пишется в С7 — здесь только схема и её проверка.
"""

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
