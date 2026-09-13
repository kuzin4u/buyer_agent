"""Оболочка: чат, кнопки, разбор намерения, прокси модели (SPEC §8.9, §8.11).

Базовый слой §8.9 написан в С5, умный слой — С7. Правило шва: core не знает слов
«чек», «SKU», «магазин» — он вызывает функции profile и matching над History.

Архитектурное требование, не подлежащее смягчению (SPEC §8.9): ЛЛМ — надстройка
над детерминированным ядром, не замена. Все числа считает ядро. Без ключа агент
работает на базовом слое.

Состав слоя:
    session.py     состояние принципала в процессе: история, профиль, каталог
    scenarios.py   восемь функций ядра как вызываемые сценарии, без слов интерфейса
    parse.py       базовый слой §8.9: кнопки и разбор запроса регулярками
"""

from .session import Session
from .scenarios import SCENARIOS, Scenario, run_scenario, scenario
from .parse import Intent, Parser, load_intents

__all__ = ["Session", "SCENARIOS", "Scenario", "run_scenario", "scenario",
           "Intent", "Parser", "load_intents"]
