"""Умный слой §8.9: модель разбирает запрос и объясняет, числа считает ядро.

Архитектурное требование, не подлежащее смягчению (SPEC §8.9): ЛЛМ — надстройка
над детерминированным ядром, не замена. Здесь это исполнено буквально.

**Модель не получает данных для счёта — она получает готовый ответ ядра.**
Порядок такой:

    запрос → модель выбирает функцию и параметры (вызов инструмента)
           → ФУНКЦИЮ ВЫПОЛНЯЕТ ЯДРО
           → модель объясняет результат словами
           → проверка: каждое число текста обязано быть в данных ядра
           → не прошло — отдаётся базовый слой

**Ключ живёт на сервере.** Он читается из окружения процесса ядра и в клиент не
попадает никогда (SPEC §8.9, §10). Без ключа умный слой просто выключен.

**Любая ошибка — это деградация, а не отказ.** Нет ключа, нет библиотеки, сеть
недоступна, модель ответила текстом вместо вызова, превышен лимит, проверка
чисел не прошла — во всех случаях возвращается `Smart(ok=False, reason=...)`, и
оболочка показывает базовый слой. Пользователь получает ответ всегда; умный слой
может только добавить объяснение, но не отнять ответ.
"""

import os
import time
from dataclasses import dataclass, field

from .parse import Intent
from .scenarios import SCENARIOS
from .verify import check

#: Модель по умолчанию. Меняется переменной окружения, а не правкой кода.
MODEL = os.environ.get("BUYER_AGENT_MODEL", "claude-opus-5")
#: Ключ читается из окружения СЕРВЕРА. В браузер и в бота он не уходит.
KEY_ENV = "ANTHROPIC_API_KEY"

#: Лимиты. Умный слой необязателен, и упереться в него должно быть дёшево.
MAX_QUERY_CHARS = 500        # запрос длиннее — это не вопрос, а вставка текста
MAX_CALLS_PER_MINUTE = 20    # на процесс: у ядра один принципал (SPEC §8.10)
MAX_OUTPUT_TOKENS = 800      # объяснение короткое по определению
TIMEOUT_SECONDS = 20.0

SYSTEM_ROUTE = """Ты разбираешь запросы пользователя к агенту-закупщику и
выбираешь, какую функцию ядра вызвать. Ты НЕ отвечаешь на вопрос сам и НЕ
считаешь никаких чисел: все числа считает ядро.

Выбери ровно один инструмент и передай параметры, которые явно следуют из
запроса. Если запрос не ложится ни на одну функцию, ответь словом НЕТ и ничего
не вызывай."""

SYSTEM_EXPLAIN = """Ты объясняешь пользователю результат, который посчитал
агент-закупщик. Правила жёсткие.

1. Все числа уже посчитаны. Бери их из данных ДОСЛОВНО. Не складывай, не дели,
   не пересчитывай в год или в месяц, не округляй иначе, чем дано.
2. Если числа нет в данных — не пиши его. Лучше сказать без числа.
3. Два-четыре предложения, по-русски, без списков и заголовков.
4. Не обещай того, чего в данных нет, и не советуй сверх того, что посчитано.

Ответ пользователя будет отброшен целиком, если в нём найдётся число, которого
нет в данных."""


@dataclass(frozen=True)
class Smart:
    """Итог умного слоя. `ok=False` означает «показывай базовый слой»."""

    ok: bool
    intent: object = None          # Intent, если модель выбрала функцию
    payload: object = None         # что посчитало ядро
    text: str = ""                 # объяснение, прошедшее проверку
    reason: str = None             # почему не получилось
    verdict: object = None         # итог проверки чисел
    usage: dict = field(default_factory=dict)

    @property
    def degraded(self):
        return not self.ok


def available():
    """Включён ли умный слой. Без ключа — нет, и это нормальный режим работы."""
    return bool(os.environ.get(KEY_ENV))


def tool_schemas(scenarios=None):
    """Реестр сценариев → схемы инструментов для модели.

    Схемы строятся из того же реестра, что обслуживает кнопки и URL: разойтись
    им нельзя, иначе модель начнёт звать функции, которых нет.
    """
    scenarios = scenarios or SCENARIOS
    types = {
        "period": {"type": "string", "enum": ["day", "week", "month"],
                   "description": "срок корзины"},
        "budget": {"type": "number", "description": "сумма в рублях"},
        "amount": {"type": "number", "description": "сумма в рублях"},
        "by": {"type": "string",
               "enum": ["year", "month", "venue", "dept", "group", "segment"],
               "description": "разрез трат"},
        "months": {"type": "integer", "description": "ширина окна в месяцах"},
        "limit": {"type": "integer", "description": "сколько строк показать"},
        "growth": {"type": "boolean", "description": "показать, где траты растут"},
        "all": {"type": "boolean", "description": "по всем товарам, а не корзине"},
        "blended": {"type": "boolean", "description": "включая нераспознанные марки"},
        "asof": {"type": "string", "description": "дата отсчёта, ГГГГ-ММ-ДД"},
    }
    out = []
    for scenario in scenarios.values():
        properties = {name: types[name] for name in scenario.params
                      if name in types}
        out.append({
            "name": scenario.id,
            "description": f"Функция ядра {scenario.spec}",
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": list(scenario.required),
                "additionalProperties": False,
            },
            # Схема обязана соблюдаться: параметр не из списка сломает вызов
            # функции ядра, а «почти правильный» вызов хуже отсутствия вызова.
            "strict": True,
        })
    return out


class Limiter:
    """Ограничитель частоты на процесс.

    Состояние процессное, а не пользовательское: у ядра один принципал
    (SPEC §8.10), и считать по пользователям здесь нечего. Когда принципалов
    станет несколько, ограничитель переедет в хранилище вместе с ними.
    """

    def __init__(self, limit=MAX_CALLS_PER_MINUTE, window=60.0):
        self.limit = limit
        self.window = window
        self.calls = []

    def allow(self, now=None):
        now = now if now is not None else time.monotonic()
        self.calls = [t for t in self.calls if now - t < self.window]
        if len(self.calls) >= self.limit:
            return False
        self.calls.append(now)
        return True


class Client:
    """Тонкая обёртка над официальным SDK. Создаётся лениво и только с ключом."""

    def __init__(self, model=MODEL, timeout=TIMEOUT_SECONDS):
        self.model = model
        self.timeout = timeout
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import anthropic          # не импортируется, пока умный слой выключен
            self._client = anthropic.Anthropic(timeout=self.timeout)
        return self._client

    def call(self, system, messages, tools=None, max_tokens=MAX_OUTPUT_TOKENS):
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            # Разбор запроса и пересказ готовых чисел — простые задачи;
            # низкое усилие здесь дешевле и быстрее, а качество не страдает.
            "output_config": {"effort": "low"},
        }
        if tools:
            kwargs["tools"] = tools
            # Не принудительный вызов: на части моделей он отвергается, а нам
            # достаточно ответа текстом — он означает «не понял», и это законный
            # исход, ведущий в базовый слой.
            kwargs["tool_choice"] = {"type": "auto"}
        return self.client.messages.create(**kwargs)


def _text_of(response):
    return "".join(block.text for block in response.content
                   if getattr(block, "type", None) == "text").strip()


def _tool_call(response):
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            return block.name, dict(block.input or {})
    return None, None


def _usage(response):
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    return {"input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None)}


def route(query, client=None, scenarios=None, tools=None):
    """Запрос → намерение, выбранное моделью. Не понял — `Intent` без сценария.

    Модель выбирает ТОЛЬКО функцию и параметры. Ответить на вопрос сама она не
    может: данных у неё нет, они появятся на следующем шаге и уже посчитанными.
    """
    scenarios = scenarios or SCENARIOS
    client = client or Client()
    response = client.call(SYSTEM_ROUTE, [{"role": "user", "content": query}],
                           tools=tools or tool_schemas(scenarios))
    name, params = _tool_call(response)
    if name is None or name not in scenarios:
        return Intent(query=query), _usage(response)
    scenario = scenarios[name]
    accepted = scenario.accepts(params)
    return Intent(query=query, scenario=name, params=accepted,
                  matched=("выбрано моделью",),
                  missing=scenario.missing(accepted)), _usage(response)


def explain(query, scenario, payload, client=None):
    """Готовый ответ ядра → объяснение словами, прошедшее проверку чисел.

    Числа сверяются по ТОМУ ЖЕ списку фактов, который модели и дали, а не по
    всему ответу ядра. Так строже и честнее: любое число в объяснении обязано
    быть среди тех, что мы вручили, — иначе оно откуда-то выведено, а выводить
    числа модели не разрешено (§8.9).
    """
    client = client or Client()
    given = facts(scenario, payload)
    message = (f"Вопрос пользователя: {query}\n\n"
               f"Данные, которые посчитал агент:\n{given}")
    response = client.call(SYSTEM_EXPLAIN, [{"role": "user", "content": message}])
    text = _text_of(response)
    return text, check(text, given), _usage(response)


def facts(scenario, payload):
    """Те же числа, что видит человек в заголовке ответа, — и ничего больше.

    Это не оформление, а защита от подмены смысла, которую проверка чисел поймать
    не может. В ответе 8.6 лежит `single_totals` — сколько стоила бы корзина у
    каждой площадки, причём у каждой по СВОЕМУ набору строк. «Магнит 239 ₽»
    оттуда выглядит как лучшая цена, хотя покрывает одну строку из четырёх. Число
    настоящее, посчитано ядром, проверку оно пройдёт — а вывод из него будет
    ложный.

    Поэтому модель получает ровно то, что в этом же ответе показано человеку:
    заголовок страницы, а не внутренности расчёта.
    """
    builder = FACTS.get(scenario)
    if builder is None:
        return summarize(payload)
    try:
        rows = builder(payload)
    except Exception:
        return summarize(payload)
    return "\n".join(f"{label}: {value}" for label, value in rows
                      if value is not None)


def _money(value):
    return f"{value:,.0f}".replace(",", " ") if value is not None else None


def _facts_profile(p):
    profile = p["profile"]
    return [("период истории", f"{profile.span[0][:10]} … {profile.span[1][:10]}"),
            ("продуктовых сделок", profile.txns),
            ("потрачено, ₽", _money(profile.spend)),
            ("средний чек, ₽", _money(profile.avg_txn)),
            ("основной магазин", profile.main_venue),
            ("его доля трат", f"{profile.main_venue_share:.0%}"),
            ("групп в постоянной корзине", len(profile.staples))]


def _facts_basket(p):
    basket = p["basket"]
    rows = [("корзина", p["title"]),
            ("позиций", len(basket)),
            ("итого, ₽", _money(basket.total))]
    rows += [(f"позиция {line.group}, ₽", _money(line.amount))
             for line in basket.lines[:8]]
    return rows


def _facts_budget(p):
    fit = p["fit"]
    rows = [("бюджет, ₽", _money(p["amount"])),
            ("как обычно, ₽", _money(fit.total_before)),
            ("итого, ₽", _money(fit.total)),
            ("уложились", "да" if fit.fits else "нет"),
            ("замен", len(fit.swaps)),
            ("сэкономлено заменами, ₽", _money(fit.saved_by_swaps)),
            ("выброшено групп", len(fit.dropped)),
            ("сэкономлено выбросом, ₽", _money(fit.saved_by_drops))]
    rows += [(f"замена {line.group}", f"{line.was} → {line.label}, "
              f"−{_money(line.swap.saving)} ₽") for line in p["swapped"][:5]]
    return rows


def _facts_lapsed(p):
    rows = [("дата отсчёта", p["asof"]),
            ("групп к докупке", len(p["items"])),
            ("ориентировочно, ₽", _money(p["total"]))]
    rows += [(f"{x.group}: дней без покупки", x.days_since) for x in p["items"][:8]]
    return rows


def _facts_prices(p):
    summary = p["summary"]
    rows = [("рядов", summary["keys"] if summary else 0),
            ("подорожало", summary["up"] if summary else 0),
            ("подешевело", summary["down"] if summary else 0),
            ("типичное изменение", f"{summary['median_pct']:+.0%}" if summary else None)]
    rows += [(f"{t.key.label}", f"{t.first.median:.0f} → {t.last.median:.0f} "
              f"({t.delta_pct:+.0%})") for t in p["trends"][:6]]
    return rows


def _facts_venues(p):
    if p.get("route") is not None:
        route = p["route"]
        rows = [("в одном месте", route.best_single),
                ("в одном месте, ₽", _money(route.baseline_total)),
                ("врозь, ₽", _money(route.split_total)),
                ("экономия, ₽", _money(route.saving_abs)),
                ("экономия, доля", f"{route.saving_pct:.0%}"),
                ("разведено строк", len(route.lines)),
                ("всего строк", route.considered)]
        rows += [(f"{line.key.label}",
                  f"{line.venue}, {line.unit_price:.0f}, "
                  f"экономия {_money(line.saving)} ₽"
                  + (f", цене {line.months_old:.0f} мес"
                     if line.months_old is not None else ""))
                 for line in route.lines[:6]]
        return rows
    rows = [("сравнимых товаров", p["total"])]
    rows += [(c.key.label,
              " · ".join(f"{x.venue} {x.median:.0f}" for x in c.prices)
              + (f" (цене {c.months_old:.0f} мес)"
                 if c.months_old is not None else ""))
             for c in p["rows"][:8]]
    return rows


def _facts_choose(p):
    rows = [("основание итога", ", ".join(p["basis"]))]
    rows += [(s.venue, f"итог {s.total:.2f}, индекс цены {s.price_index:.2f}, "
              f"товаров {s.keys}") for s in p["scores"][:6]]
    return rows


def _facts_spending(p):
    summary = p["summary"]
    rows = [("всего, ₽", _money(summary["total"])),
            ("месяцев", summary["months"]),
            ("типичный месяц, ₽", _money(summary["median_month"])),
            ("разрез", p["by"])]
    if p["growth"]:
        rows += [(str(t.key), f"было {_money(t.before)} → стало {_money(t.after)} ₽")
                 for t in p["trends"][:8]]
    else:
        rows += [(str(b.key), f"{_money(b.amount)} ₽, доля {b.share:.1%}")
                 for b in p["buckets"][:8]]
    return rows


#: Сценарий → что из его ответа увидит модель. Ровно то же, что заголовок
#: страницы показывает человеку.
FACTS = {
    "profile": _facts_profile,
    "basket": _facts_basket,
    "budget": _facts_budget,
    "lapsed": _facts_lapsed,
    "prices": _facts_prices,
    "venues": _facts_venues,
    "choose": _facts_choose,
    "spending": _facts_spending,
}


def summarize(payload, limit=60):
    """Запасной пересказ для сценария без своего списка фактов."""
    lines = []

    def walk(value, prefix="", depth=0):
        if len(lines) >= limit or depth > 3:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, f"{prefix}{key}.", depth + 1)
            return
        if isinstance(value, (list, tuple)):
            for index, item in enumerate(value[:10]):
                walk(item, f"{prefix}{index}.", depth + 1)
            return
        fields = getattr(value, "__dataclass_fields__", None)
        if fields:
            for name in fields:
                walk(getattr(value, name, None), f"{prefix}{name}.", depth + 1)
            return
        if value is None or isinstance(value, bool):
            return
        lines.append(f"{prefix.rstrip('.')}: {value}")

    walk(payload)
    return "\n".join(lines[:limit])


def answer(query, session, client=None, limiter=None, scenarios=None,
           fallback=None):
    """Полный проход умного слоя. Любая беда возвращает `ok=False`.

    `fallback` — намерение базового слоя. Если модель не выбрала функцию, а
    регулярки выбрали, берётся их разбор: умный слой добавляет объяснение, но не
    отнимает ответ.
    """
    scenarios = scenarios or SCENARIOS
    if not available() and client is None:
        return Smart(ok=False, reason="ключ модели не задан: работает базовый слой")
    if not query or len(query) > MAX_QUERY_CHARS:
        return Smart(ok=False,
                     reason=f"запрос длиннее {MAX_QUERY_CHARS} символов")
    if limiter is not None and not limiter.allow():
        return Smart(ok=False, reason="слишком часто: подождите минуту")

    usage = {}
    try:
        intent, spent = route(query, client=client, scenarios=scenarios)
        usage.update(spent)
        if not intent.understood and fallback is not None and fallback.understood:
            intent = fallback
        if not intent.understood:
            return Smart(ok=False, intent=intent, usage=usage,
                         reason="модель не выбрала функцию ядра")
        if intent.missing:
            return Smart(ok=False, intent=intent, usage=usage,
                         reason=f"не хватает параметров: {', '.join(intent.missing)}")

        # Считает ЯДРО. Модель к этому моменту уже отработала и больше не влияет
        # ни на одно число в ответе.
        payload = scenarios[intent.scenario](session, **intent.params)

        text, verdict, spent = explain(query, intent.scenario, payload,
                                       client=client)
        usage.update(spent)
        if not verdict.ok:
            return Smart(ok=False, intent=intent, payload=payload, text=text,
                         verdict=verdict, usage=usage, reason=verdict.reason)
        if not text:
            return Smart(ok=False, intent=intent, payload=payload, usage=usage,
                         reason="модель не дала объяснения")
        return Smart(ok=True, intent=intent, payload=payload, text=text,
                     verdict=verdict, usage=usage)
    except Exception as error:          # сеть, ключ, лимит провайдера, что угодно
        # Умный слой необязателен: любая его поломка обязана быть незаметна для
        # ответа. Тип ошибки сохраняется для диагностики, но наружу уходит одно:
        # «работает базовый слой».
        return Smart(ok=False, usage=usage,
                     reason=f"{type(error).__name__}: {error}")
