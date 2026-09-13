"""Оформление ответа ядра для мессенджера. Здесь нет ни одного вычисления.

Всё, что делает этот модуль, — переносит строки из ответа ядра в текст
сообщения. Числа приходят готовыми: сложить два из них означало бы завести в
боте логику, которой там быть не должно (ОА-1).
"""

#: Длина сообщения в Telegram ограничена; длинный ответ обрывается со ссылкой в
#: веб — там он показан таблицей, а не простынёй текста (Р-1).
MAX_CHARS = 3500


def escape(text):
    """Экранирование для HTML-разметки Telegram."""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def answer(payload, web_url=None):
    """Ответ ядра → текст сообщения."""
    if not payload.get("understood"):
        lines = ["Не понял запрос."]
        if payload.get("reason"):
            lines.append(escape(payload["reason"]))
        lines.append("\nВот что я умею — выберите кнопкой:")
        return "\n".join(lines)

    if payload.get("missing"):
        return ("Понял, что нужно, но не хватает числа: "
                + escape(", ".join(payload["missing"]))
                + ".\nНапишите, например: «уложись в 2000».")

    lines = []
    if payload.get("text"):
        # Объяснение модели прошло проверку чисел на стороне ядра: любое число
        # в нём есть в данных (SPEC §8.9).
        lines.append(escape(payload["text"]))
        lines.append("")
    facts = payload.get("facts") or ""
    if facts:
        lines.append("<pre>" + escape(facts) + "</pre>")
    if web_url:
        lines.append(f'\n<a href="{web_url}">Подробно в вебе</a>')
    text = "\n".join(lines)
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS] + "…"


def reminder(items):
    """Напоминания 8.4 → текст push-сообщения."""
    if not items:
        return None
    lines = ["<b>Давно не покупали</b>"]
    for item in items:
        lines.append(
            f"• {escape(item['group'])} — {item['days_since']} дн назад, "
            f"обычно раз в {item['median_gap_days']} дн "
            f"({escape(item['label'])})")
    return "\n".join(lines)
