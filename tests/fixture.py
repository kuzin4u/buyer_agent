"""Общий прогон для тестов.

Конвейер по 19 056 позициям занимает около секунды. Пока классов было два,
это не имело значения; сейчас их восемь, и без кэша прогон тестов растягивался
до полуминуты. Здесь он делается один раз на весь запуск.
"""

import os
import sys
from functools import lru_cache

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from agent.config import Config, dataset_path                       # noqa: E402
from agent.settings import Settings                                 # noqa: E402
from agent.adapters.receipts_fns import Pipeline, load_receipts     # noqa: E402
from agent.adapters.receipts_fns.pipeline import to_history         # noqa: E402
from agent.profile import build                                     # noqa: E402


@lru_cache(maxsize=None)
def receipts():
    return tuple(load_receipts(dataset_path(BASE)))


@lru_cache(maxsize=None)
def run(include_candidates=False):
    return Pipeline(Config.load(BASE),
                    include_candidates=include_candidates).run(list(receipts()))


@lru_cache(maxsize=None)
def history(include_candidates=False):
    return to_history(run(include_candidates))


@lru_cache(maxsize=None)
def profile(include_candidates=False):
    return build(history(include_candidates), Settings(
        include_candidates=include_candidates))
