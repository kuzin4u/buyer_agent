"""Сборка SKU — единицы, внутри которой цена сопоставима (SPEC §4).

Два вида, и они не взаимозаменяемы (docs/DECISIONS.md Р-3):

  штучный  ключ = группа · бренд · жирность · фасовка, цена ₽/кг и ₽/упаковку
  весовой  ключ = группа · название,                   цена уже ₽/кг

У весового бренд и штучная фасовка не заполняются: они неприменимы, а не
неизвестны, и в диагностике не должны смешиваться с нераспознанными.
"""

from ...history import ItemKey, UNKNOWN
from .rules import UNIT_LABEL

#: заполнитель нераспознанного различителя определён в нейтральной модели:
#: он нужен и профилю, чтобы не считать «марка неизвестна» одним товаром


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

    Название приходит уже без хвоста единицы измерения: «БАНАНЫ», «БАНАНЫ 1КГ»
    и «БАНАНЫ ВЕС 1КГ» — это один ключ (normalize.bulk_key, правило в
    normalization.json → weighted_goods.key_name; пробел П-1 закрыт в С3).
    """
    return ItemKey(
        kind="bulk",
        group=group_id,
        parts=(name,),
        unit="kg",
        label=name,
    )
