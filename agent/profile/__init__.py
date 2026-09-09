"""Построение профиля из истории (SPEC §8.1–8.8).

profile получает History, а не чеки, и слов «чек», «SKU» и «магазин» в своей
сигнатуре не имеет (SPEC §8.10).

НЕ выносить в отдельную библиотеку: пока агент один, общее угадывается
неправильно — оно становится видно, когда появится второй (SPEC §9).
"""

from ..history import History
from .build import Profile, GroupStat, VenueStat, build

__all__ = ["History", "Profile", "GroupStat", "VenueStat", "build"]
