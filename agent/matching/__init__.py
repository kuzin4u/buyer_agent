"""Подбор позиций из каталога (SPEC §8.3, §8.10). Наполняется в С4.

Здесь заранее уложен единственный шов, который дорого добавлять потом:
**горизонт**. Он аргумент функции подбора, но получить его можно только из
режима позиции — злоупотребление становится невозможным, а не запрещённым
(SPEC §8.10).
"""

from dataclasses import dataclass
from enum import Enum


class Mode(Enum):
    """Откуда позиция — и, следовательно, чему про неё можно верить."""

    REPEATABLE = "repeatable"   # каталог Системы: поток сделок, есть исполнение
    ONE_OFF = "one_off"         # каталог рынка: одна сделка, верить только цене


@dataclass(frozen=True)
class Horizon:
    """Горизонт подбора. Конструируется только из режима каталога."""

    mode: Mode

    @classmethod
    def of(cls, catalog_mode):
        return cls(mode=Mode(catalog_mode))

    @property
    def allows_substitution(self):
        """Замену привычного SKU можно предлагать только там, где исполнение
        обеспечено участием продавца. В разовой среде позицию показываем, но
        замену не предлагаем (SPEC §8.10)."""
        return self.mode is Mode.REPEATABLE


def check_horizon(catalog_mode, horizon):
    """Страховка шва: горизонт обязан быть выведен из этого же каталога.

    Без неё ничто не помешает выставить агенту покупателя продавцовский
    горизонт, и останется одно обещание так не делать.
    """
    expected = Horizon.of(catalog_mode)
    if horizon != expected:
        raise ValueError(
            f"горизонт {horizon.mode.value} не выведен из каталога "
            f"{expected.mode.value}: горизонт — производная режима позиции")
    return horizon


#: Для первой версии закупщика доступен только собственный каталог покупок,
#: поэтому весь режим — повторяемый (SPEC §8.10).
OWN_HISTORY_MODE = Mode.REPEATABLE
