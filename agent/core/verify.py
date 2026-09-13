"""Проверка ответа модели: любое число обязано быть в данных ядра (SPEC §8.9).

Требование §8.9 не смягчается: «ЛЛМ разбирает запрос и объясняет результат, но
не считает и не выдумывает цифр». Проверить это можно механически, и здесь это
сделано: из текста вынимаются все числа, и каждое сверяется с тем, что посчитало
ядро. Не нашлось — ответ отбраковывается целиком, и пользователь получает
базовый слой.

**Почему целиком, а не «подчистить».** Число, которого нет в данных, означает,
что модель считала сама. Если она посчитала одно число, доверия к остальному
тексту нет: он писался тем же способом. Вырезать неверное число и отдать остаток
значит отдать текст, про который мы уже знаем, что он сочинён.

**Почему сверка по значению, а не по строке.** Ядро отдаёт 1246.13, человек
читает «1 246 ₽», а доля 0.2734 печатается как «27%». Сверять строки значило бы
отбраковывать верные ответы и пропускать неверные. Поэтому число из текста
сопоставляется с допустимым по ЗНАЧЕНИЮ, с точностью, с которой оно написано.
"""

import re
from dataclasses import dataclass, field

#: Число в тексте: 1 246, 1246.5, 1246,5, 27%, −161. Разделителем тысяч бывает
#: пробел и неразрывный пробел — оболочка печатает именно так.
RE_NUMBER = re.compile(r"[-−+]?\d[\d   ]*(?:[.,]\d+)?\s*%?")

#: Года и мелкие числа встречаются в любом тексте как часть речи: «три магазина»,
#: «в 2026 году». Год проверяется наравне со всеми — он есть в данных, если
#: ядро его посчитало. А вот числа от 0 до 12 разрешены всегда: это счёт
#: предметов в фразе, и запрещать их значит запрещать русский язык.
SMALL = 12

#: Допуск при сравнении: ядро отдаёт 1246.13, текст пишет «1 246». Сравнение
#: идёт с точностью, с какой число написано, плюс половина последнего разряда.
EPSILON = 1e-9


@dataclass(frozen=True)
class Verdict:
    """Итог проверки. `ok` — можно отдавать пользователю."""

    ok: bool
    invented: tuple = ()      # числа, которых нет в данных ядра
    checked: int = 0
    allowed: frozenset = field(default_factory=frozenset)

    @property
    def reason(self):
        if self.ok:
            return None
        return ("в ответе есть числа, которых нет в данных ядра: "
                + ", ".join(str(n) for n in self.invented))


def parse(token):
    """Число из текста → (значение, в процентах ли, сколько знаков после запятой)."""
    raw = token.strip()
    percent = raw.endswith("%")
    raw = raw.rstrip("% ").replace("−", "-")
    raw = re.sub(r"[   ]", "", raw).replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        return None
    digits = len(raw.split(".")[1]) if "." in raw else 0
    return value, percent, digits


def numbers(value, seen=None):
    """Все числа, которые ядро посчитало, — рекурсивно по результату сценария.

    Обходятся словари, списки, кортежи и датаклассы: результат сценария — это
    смесь всего перечисленного, и ограничиться верхним уровнем значит запретить
    модели упоминать то, что лежит в строке таблицы.
    """
    out = set()
    seen = seen if seen is not None else set()
    if id(value) in seen:
        return out
    seen.add(id(value))

    if isinstance(value, bool) or value is None:
        return out
    if isinstance(value, (int, float)):
        out.add(float(value))
        return out
    if isinstance(value, str):
        # Числа внутри строки — тоже числа ядра: и в списке фактов, который
        # получает модель, и в названии товара «Савушкин · 9% · 0,3 кг». Их
        # человек видит, значит модели можно их повторить.
        for match in RE_NUMBER.finditer(value):
            parsed = parse(match.group(0))
            if parsed is not None:
                number, percent, _digits = parsed
                out.add(number / 100 if percent else number)
                out.add(number)
        return out
    if isinstance(value, dict):
        for key, item in value.items():
            out |= numbers(key, seen) | numbers(item, seen)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            out |= numbers(item, seen)
        return out

    fields = getattr(value, "__dataclass_fields__", None)
    if fields:
        for name in fields:
            out |= numbers(getattr(value, name, None), seen)
        # Свойства датакласса — тоже числа ядра: доли и экономия чаще всего
        # именно там, а не в полях.
        for name, attribute in type(value).__dict__.items():
            if isinstance(attribute, property):
                try:
                    out |= numbers(getattr(value, name), seen)
                except Exception:      # свойство может требовать чего-то ещё
                    continue
    return out


def derived(values):
    """Числа, которые человек увидит вместо исходных.

    Доля 0.2734 печатается как «27%», сумма 1246.13 — как «1 246». Это не
    послабление проверки, а её условие: без производных форм отбраковывался бы
    любой верно оформленный ответ.
    """
    out = set()
    for value in values:
        out.add(value)
        out.add(abs(value))
        out.add(round(value))
        out.add(round(value, 1))
        out.add(round(value, 2))
        if abs(value) <= 1:            # доля → проценты
            out.add(round(value * 100))
            out.add(round(value * 100, 1))
        out.add(round(value / 1000, 1))    # тысячи рублей
    return out


def check(text, payload, small=SMALL):
    """Сверить каждое число текста с данными ядра.

    Число считается подтверждённым, если совпадает с каким-нибудь числом ядра с
    точностью, с какой написано: «1 246» подтверждается значением 1246.13, а
    «1 247» — нет.
    """
    allowed = derived(numbers(payload))
    invented, checked = [], 0
    for match in RE_NUMBER.finditer(text or ""):
        parsed = parse(match.group(0))
        if parsed is None:
            continue
        value, percent, digits = parsed
        checked += 1
        if abs(value) <= small and not percent and digits == 0:
            continue                  # счёт предметов в фразе, не данные
        tolerance = 0.5 * (10 ** -digits) + EPSILON
        candidates = {value, abs(value)}
        if percent:
            candidates |= {value / 100, abs(value) / 100}
        if any(abs(candidate - good) <= tolerance
               for candidate in candidates for good in allowed):
            continue
        invented.append(match.group(0).strip())
    return Verdict(ok=not invented, invented=tuple(invented), checked=checked,
                   allowed=frozenset(allowed))
