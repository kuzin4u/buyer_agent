"""Общий прогон для тестов и разделение набора на быстрый и полный.

Конвейер по 19 056 позициям занимает около секунды. Пока классов было два, это
не имело значения; сейчас их два десятка, и без кэша прогон растягивался до
получаса. Здесь он делается один раз на весь запуск.

**Быстрый набор и полный.** Кэш спасает только тех, кто читает ОДНУ историю. С
С6 появились тесты, которые прогоняют конвейер заново с изменёнными конфигами —
иначе эффект пополнения словаря не измерить, — и каждый такой тест стоит секунду
с лишним. Их набралось столько, что полный прогон вырос до 137 секунд, и это уже
мешает: тест, который не запускают, не ловит ничего.

Поэтому такие тесты помечены `@slow`, и набор делится на два:

    python3 tests/run.py            быстрый: всё, кроме пересборок (~25 с)
    python3 tests/run.py --full     полный, перед коммитом

Метка ставится по проверяемому признаку, а не на глаз: медленный тест — это тот,
который пересобирает историю. Держать её честной помогает `tests/run.py --audit`:
он сверяет время каждого теста с меткой и ругается на расхождение.
"""

import os
import sys
import unittest
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


#: Быстрый набор — переменная окружения, а не аргумент: метку читают и сами
#: тесты через декоратор, и внешний запуск (`python3 -m unittest`), у которого
#: своих аргументов нет.
FAST_ENV = "BUYER_AGENT_FAST"

SLOW_REASON = ("медленный тест: пересобирает историю конвейером. "
               "Полный набор: python3 tests/run.py --full")


def fast_only():
    return os.environ.get(FAST_ENV) == "1"


def slow(target):
    """Пометить тест или класс как медленный — пересобирающий историю.

    Признак проверяемый: если тест прогоняет `Pipeline` с другими конфигами, он
    медленный. Всё, что читает общий кэшированный прогон, быстрое.
    """
    target.buyer_agent_slow = True
    return unittest.skipIf(fast_only(), SLOW_REASON)(target)
