"""Базовый слой §8.9: кнопки и разбор запроса регулярками, без ЛЛМ.

Работает офлайн и без ключей — это требование §8.9, а не удобство. ЛЛМ появится
в С7 как надстройка: он будет разбирать формулировки, которые здесь не
разобрались, и объяснять результат словами. Числа и здесь, и там считает ядро.

**Почему разбор объясняет себя.** Свободный запрос всегда можно понять неверно,
и пользователь обязан видеть, как его поняли: `Intent.matched` хранит обрывки,
по которым сценарий узнан, а `Intent.params` — что из запроса извлечено.
Молчаливо подставленный параметр — это неотличимая от правды ошибка: «корзина на
2000» и «корзина на неделю» дают совершенно разные ответы.

**Чего здесь намеренно нет.** Ни одной формулировки: все они в
`config/intents.json`. Код знает, КАК сопоставлять, конфиг — ЧТО сопоставлять.
"""

import json
import os
import re
from dataclasses import dataclass, field

from ..config import BASE

FILENAME = "config/intents.json"


def load_intents(base=BASE):
    """Конфиг формулировок. Отсутствие файла — ошибка: без него нет кнопок."""
    with open(os.path.join(base, FILENAME), encoding="utf-8") as f:
        return json.load(f)


def normalize(query):
    """Привести запрос к виду, в котором его сопоставляют.

    Ё→Е — то же правило, что у названий товаров (П-3): «недёля» и «неделя»
    должны вести в один сценарий, и заводить для этого два словаря незачем.
    """
    return re.sub(r"\s+", " ", (query or "").lower().replace("ё", "е")).strip()


@dataclass(frozen=True)
class Intent:
    """Чем оказался запрос. `scenario is None` — не поняли, и это честный ответ."""

    query: str
    scenario: str = None
    params: dict = field(default_factory=dict)
    matched: tuple = ()          # обрывки, по которым узнан сценарий
    missing: tuple = ()          # чего не хватает, чтобы выполнить
    ambiguous: tuple = ()        # другие сценарии, подошедшие не хуже

    @property
    def understood(self):
        return self.scenario is not None

    @property
    def ready(self):
        return self.understood and not self.missing


class Parser:
    """Разбор запроса по конфигу формулировок."""

    def __init__(self, intents=None, base=BASE):
        self.intents = intents or load_intents(base)
        self.params = self.intents.get("params", {})
        self.scenarios = self.intents.get("scenarios", [])

    # --- кнопки ---

    def buttons(self):
        """Типовые сценарии для кнопок — в порядке, заданном конфигом."""
        return [{"id": s["id"], "title": s["title"], "hint": s.get("hint", ""),
                 "example": s.get("example", ""), "args": s.get("button", {})}
                for s in self.scenarios]

    def titles(self):
        return {s["id"]: s["title"] for s in self.scenarios}

    # --- параметры ---

    def _first_value(self, text, table):
        """Словарь {значение: [формулировки]} → первое совпавшее значение."""
        best = None
        for value, patterns in table.items():
            for pattern in patterns:
                m = re.search(pattern, text)
                if m and (best is None or m.start() < best[1]):
                    best = (value, m.start(), m.group(0))
        return best

    def extract(self, text):
        """Вытащить параметры и вернуть их вместе с тем, что было распознано.

        Порядок не произволен: «за 6 месяцев» — это ширина окна, а не 6 ₽,
        поэтому срок снимается первым и из строки удаляется. Иначе число из
        срока станет бюджетом, и ответ будет правдоподобно неверным.
        """
        params, seen = {}, []
        rest = text

        for pattern in self.params.get("months", []):
            m = re.search(pattern, rest)
            if m:
                params["months"] = int(m.group(1))
                seen.append(m.group(0))
                rest = rest[:m.start()] + " " + rest[m.end():]
                break

        period = self._first_value(text, self.params.get("period", {}))
        if period:
            params["period"] = period[0]
            seen.append(period[2])
            # Срок уже объяснил это слово; если в нём было число, оно не бюджет.
            rest = rest.replace(period[2], " ")

        by = self._first_value(text, self.params.get("by", {}))
        if by:
            params["by"] = by[0]
            seen.append(by[2])

        for flag, patterns in self.params.get("flags", {}).items():
            for pattern in patterns:
                m = re.search(pattern, text)
                if m:
                    params[flag] = True
                    seen.append(m.group(0))
                    break

        for pattern in self.params.get("amount", []):
            m = re.search(pattern, rest)
            if m:
                digits = re.sub(r"[^\d]", "", m.group(1))
                if digits:
                    params["amount"] = float(digits)
                    seen.append(m.group(0).strip())
                break

        return params, tuple(seen)

    # --- сценарий ---

    def score(self, text):
        """→ [(сценарий, сколько формулировок совпало, какие именно)].

        Ничью решает длина совпавшего, а не порядок в конфиге. «Умная корзина»
        — это сравнение магазинов (8.6), хотя слово «корзина» в ней тоже есть;
        при равном числе совпадений прав тот, чья формулировка длиннее, то есть
        конкретнее. Порядок объявления остаётся последним доводом, чтобы разбор
        был однозначным.
        """
        out = []
        for index, spec in enumerate(self.scenarios):
            hits = tuple(m.group(0) for m in
                         (re.search(p, text) for p in spec.get("patterns", []))
                         if m)
            if hits:
                out.append((spec, len(hits), hits, sum(len(h) for h in hits), index))
        out.sort(key=lambda x: (-x[1], -x[3], x[4]))
        return [(spec, n, hits) for spec, n, hits, _len, _i in out]

    def parse(self, query, scenarios=None):
        """Запрос → намерение. Не поняли — так и говорим, не угадываем.

        `scenarios` — реестр из `core.scenarios`; по нему проверяется, каких
        параметров сценарию не хватает. Без реестра разбор всё равно работает,
        просто не знает про обязательные параметры.
        """
        text = normalize(query)
        if not text:
            return Intent(query=query or "")

        params, seen = self.extract(text)
        ranked = self.score(text)
        if not ranked:
            return Intent(query=query, params=params, matched=seen)

        spec, top, hits = ranked[0]
        rivals = tuple(s["id"] for s, n, _h in ranked[1:] if n == top)

        # «Корзина на 2000» — это «уложись в 2000»: вопрос со суммой
        # предписывающий, а не описательный (Р-22). 8.2 остаётся доступной
        # кнопкой и ссылкой, но свободный запрос с суммой ведёт в 8.3.
        if spec["id"] == "basket" and params.get("amount"):
            budget = next((s for s in self.scenarios if s["id"] == "budget"), None)
            if budget is not None:
                rivals = ("basket",) + rivals
                spec = budget

        scenario = scenarios.get(spec["id"]) if scenarios else None
        chosen = dict(params)
        if scenario is not None:
            chosen = scenario.accepts(params)
            missing = scenario.missing(chosen)
        else:
            missing = ()
        return Intent(query=query, scenario=spec["id"], params=chosen,
                      matched=hits + seen, missing=missing, ambiguous=rivals)
