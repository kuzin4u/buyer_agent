"""Сборка SKU — единицы, внутри которой цена сопоставима (SPEC §4).

Два вида, и они не взаимозаменяемы (docs/DECISIONS.md Р-3):

  штучный  ключ = группа · бренд · жирность · фасовка, цена ₽/кг и ₽/упаковку
  весовой  ключ = группа · название,                   цена уже ₽/кг

У весового бренд и штучная фасовка не заполняются: они неприменимы, а не
неизвестны, и в диагностике не должны смешиваться с нераспознанными.
"""

from ...history import ItemKey
from .rules import UNIT_LABEL

#: заполнитель нераспознанного различителя; SKU он не ломает (SPEC §4)
UNKNOWN = "—"


def pack_label(unit, value):
    return f"{value:g}{UNIT_LABEL[unit]}"


def unit_sku(group_id, brand, fat, unit, pack):
    """Штучная позиция: фасовка постоянна, поэтому сопоставимы обе меры цены."""
    parts = (brand or UNKNOWN, fat or UNKNOWN, pack_label(unit, pack))
    return ItemKey(
        kind="unit",
        group=group_id,
        parts=parts,
        unit=unit,
        label=f"{parts[0]} · {parts[1]}% · {parts[2]}",
    )


def bulk_sku(group_id, name):
    """Весовая позиция: единственный оставшийся различитель — название.

    Названия сейчас не нормализованы, поэтому «БАНАНЫ», «БАНАНЫ 1КГ» и
    «БАНАНЫ ВЕС 1КГ» — три разных ключа. Это известный пробел П-1, правило
    для normalization.json появится в С3 вместе с панелью диагностики.
    """
    return ItemKey(
        kind="bulk",
        group=group_id,
        parts=(name,),
        unit="kg",
        label=name,
    )
