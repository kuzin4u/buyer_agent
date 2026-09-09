"""Адаптер: чеки ФНС → история персоны.

Знает ровно одного принципала — владельца чеков. Никакой передачи профиля
соседнему агенту внутри процесса (SPEC §8.10).
"""

from .loader import Receipt, Item, load_receipts, load_dataset, parse_fns_export
from .pipeline import Pipeline, NormalizedItem

__all__ = ["Receipt", "Item", "load_receipts", "load_dataset",
           "parse_fns_export", "Pipeline", "NormalizedItem"]
