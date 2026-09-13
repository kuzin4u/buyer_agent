"""Клиент HTTP API ядра. Единственный способ бота что-либо узнать.

Ни одного импорта из `agent.*`: бот не считает и не знает, как устроено ядро.
Если ядро недоступно, бот говорит об этом и продолжает работать — как и умный
слой, недоступность не должна выглядеть поломкой.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

#: Адрес ядра. Бот и ядро живут на одной машине (DECISIONS Р-5), но адрес всё
#: равно настраиваемый: разносить их по разным машинам — вопрос конфига.
CORE_URL = os.environ.get("BUYER_AGENT_CORE", "http://127.0.0.1:8000")
TIMEOUT = float(os.environ.get("BUYER_AGENT_CORE_TIMEOUT", "30"))


class CoreError(RuntimeError):
    """Ядро недоступно или ответило ошибкой."""


def get(path, params=None, base=None, timeout=TIMEOUT):
    url = (base or CORE_URL).rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None})
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise CoreError(f"ядро недоступно: {error}") from None
    except json.JSONDecodeError as error:
        raise CoreError(f"ядро ответило не JSON: {error}") from None


def scenarios(base=None):
    return get("/api/scenarios", base=base)


def ask(query=None, scenario=None, base=None):
    return get("/api/ask", {"q": query, "scenario": scenario}, base=base)


def notifications(base=None):
    return get("/api/notifications", base=base)


def outbox(base=None):
    """Что ядро просит отправить. Ядро же и помнит, что уже отдано (ОА-1)."""
    return get("/api/outbox", base=base)
