#!/usr/bin/env python3
"""
Проверка конфигов на реальном датасете.

Запуск:  python3 validate.py
         INCLUDE_CANDIDATES=1 python3 validate.py   — с ярусом food_candidate

Ничего не строит и не пишет — только применяет config/*.json к data/receipts.json
и печатает покрытие. Нужен, чтобы после правки любого конфига было видно, что
стало лучше, а что сломалось. Контрольные цифры — в docs/DATASET.md, эталонный
вывод — в docs/BASELINE.txt.

Логика живёт в agent/adapters/receipts_fns/ — здесь только запуск, чтобы прогон
и рабочий код не разъезжались.
"""

import os
import sys

from agent.config import Config, dataset_path
from agent.adapters.receipts_fns import Pipeline, load_receipts
from agent.adapters.receipts_fns.report import render


def main():
    include_candidates = os.environ.get("INCLUDE_CANDIDATES") == "1"
    receipts = load_receipts(dataset_path())
    run = Pipeline(Config.load(), include_candidates=include_candidates).run(receipts)
    print(render(run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
