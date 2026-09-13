#!/usr/bin/env python3
"""Запуск набора тестов: быстрый по умолчанию, полный по флагу.

    python3 tests/run.py              быстрый: всё, кроме пересборок истории
    python3 tests/run.py --full       полный, перед коммитом
    python3 tests/run.py --audit      сверить метки `@slow` с фактическим временем

Зачем деление. Тесты, которые пересобирают историю конвейером (эффект пополнения
словаря иначе не измерить), стоят секунду с лишним каждый, и полный прогон вырос
до 137 секунд. Тест, который не запускают, не ловит ничего, поэтому быстрый набор
обязан оставаться быстрым.

Зачем `--audit`. Метка `@slow` ставится человеком и поэтому врёт: со временем
быстрый тест становится медленным, а медленный — быстрым, и никто этого не
замечает. Аудит замеряет каждый тест и печатает расхождения.
"""

import os
import sys
import time
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
sys.path.insert(0, HERE)

#: Дольше этого тест считается медленным. Порог с запасом: прогон конвейера —
#: около секунды, а самый долгий «быстрый» тест на датасете укладывается в 0,3 с.
SLOW_SECONDS = 0.7


def discover():
    return unittest.defaultTestLoader.discover(start_dir=HERE, top_level_dir=HERE)


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def marked_slow(test):
    method = getattr(test, test._testMethodName, None)
    return bool(getattr(method, "buyer_agent_slow", False)
                or getattr(type(test), "buyer_agent_slow", False))


class TimingResult(unittest.TextTestResult):
    """Замеряет каждый тест внутри обычного прогона."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timings = []
        self._started = None

    def startTest(self, test):
        self._started = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test):
        super().stopTest(test)
        self.timings.append((time.perf_counter() - self._started, test))


def audit():
    """Замерить каждый тест и сверить с меткой.

    Замер идёт внутри ОДНОГО обычного прогона, а не по тесту в изоляции. Первая
    версия запускала каждый тест отдельным набором — и приписывала ему стоимость
    классовой фикстуры: веб-тесты, поднимающие клиент в `setUpClass`, выходили по
    три секунды каждый, хотя в общем прогоне платят за это один раз на класс.
    Инструмент, который так мерит, вреднее отсутствия инструмента: он предлагает
    пометить медленным то, что медленно только у него.
    """
    os.environ.pop("BUYER_AGENT_FAST", None)
    runner = unittest.TextTestRunner(verbosity=0, resultclass=TimingResult)
    result = runner.run(discover())

    # Стоимость `setUpClass` unittest приписывает ПЕРВОМУ тесту класса: у
    # классов, поднимающих веб-клиент, это секунды, и тест выглядит медленным,
    # хотя платит за соседей. Первый тест каждого класса из отчёта исключается.
    first_of_class = set()
    seen_classes = set()
    for _spent, test in result.timings:
        if type(test) not in seen_classes:
            seen_classes.add(type(test))
            first_of_class.add(id(test))

    wrong_fast, wrong_slow = [], []
    for spent, test in result.timings:
        name = f"{type(test).__name__}.{test._testMethodName}"
        if id(test) in first_of_class:
            continue
        if spent > SLOW_SECONDS and not marked_slow(test):
            wrong_fast.append((spent, name))
        if spent <= SLOW_SECONDS and marked_slow(test):
            wrong_slow.append((spent, name))

    if wrong_fast:
        print(f"\nМедленные без метки @slow (дольше {SLOW_SECONDS} с):")
        for spent, name in sorted(wrong_fast, reverse=True):
            print(f"  {spent:6.2f} с  {name}")
    if wrong_slow:
        print("\nПомечены @slow, но быстрые — метку можно снять:")
        for spent, name in sorted(wrong_slow, reverse=True):
            print(f"  {spent:6.2f} с  {name}")
    if not wrong_fast and not wrong_slow:
        print("Метки совпадают с замерами.")
    print(f"\nВсего тестов: {result.testsRun}, "
          f"помечено медленными: {sum(1 for _s, t in result.timings if marked_slow(t))}")
    return 0 if result.wasSuccessful() else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--audit" in argv:
        return audit()

    full = "--full" in argv
    os.environ["BUYER_AGENT_FAST"] = "0" if full else "1"
    started = time.perf_counter()
    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(discover())
    spent = time.perf_counter() - started

    print(f"\n{'полный' if full else 'быстрый'} набор, {spent:.1f} с")
    if not full:
        print("Пропущены тесты, пересобирающие историю. Перед коммитом: "
              "python3 tests/run.py --full")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
