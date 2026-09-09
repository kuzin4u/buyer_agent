"""Конвейер нормализации (SPEC §11). Порядок операций — часть логики.

    гомоглифы (пословно) → регистр → исключения → магазин → группа
    → весовой по дробному количеству → фасовка → SKU

Каждая функция чистая: принимает Rules, ничего не пишет и не кэширует.
"""

from collections import defaultdict

from .rules import (DRINK_GROUPS, LIQUID_GROUPS, PACK_RULES, PIECES_RANGE,
                    RE_BARE_LITRES, RE_BARE_NUMBER, RE_CYRILLIC, RE_DIMS,
                    RE_FAT, RE_PIECES, RE_WORD)


def fix_homoglyphs(rules, s):
    """Пословно: латинский гомоглиф чинится только в словах, где есть кириллица.

    Сплошная замена ломает честно латинские названия — PATERRA → РАТЕRRА,
    DEL. → DЕL., R.SP. → R.SР. Проверено на данных (SPEC §5.1).
    """
    return RE_WORD.sub(
        lambda m: (m.group(0).translate(rules.homoglyphs)
                   if RE_CYRILLIC.search(m.group(0)) else m.group(0)),
        s)


def clean(rules, name):
    """Отображаемое название: гомоглифы → очистка ввода → регистр → обрезка.

    Очистка ввода снимает то, что приклеила выгрузка, а не то, чего мы не знаем
    про товар: префикс «номер строки: артикул» и слипшиеся пробелы. Это разбор
    формата, поэтому место правила — слой ввода, а не словари.

    Ё здесь сохраняется: пользователь должен видеть название так, как оно
    написано в чеке (normalization.json → case).
    """
    s = fix_homoglyphs(rules, name)
    for _name, rx, replacement in rules.cleanup:
        s = rx.sub(replacement, s)
    if rules.upper:
        s = s.upper()
    return s.strip() if rules.trim else s


def fold(rules, text):
    """Сопоставительная форма названия: Ё складывается в Е.

    Применяется ко всему, что сравнивается, — категории, бренды, магазины,
    ключ SKU, — и никогда к тому, что показывается (SPEC §11, конфиг → case).
    """
    return text.translate(rules.fold)


def classify_shop(rules, raw):
    """→ (каноническое имя | None, ярус). Ярусов три плюс unknown (SPEC §7)."""
    if not raw or raw.strip() in ("", "не указан"):
        return None, "unknown"
    up = fold(rules, clean(rules, raw))
    for rule, rx in rules.shops:
        if rx.search(up):
            return rule["canonical"], rule["tier"]
    return raw, "unknown"


def classify_item(rules, name):
    """→ группа или None. Порядок правил значим: яйцо стоит перед сметаной,
    иначе «ЯЙЦО СМЕТ С0 20ШТ» перехватывается правилом сметаны (SPEC §5)."""
    for group, rx in rules.categories:
        if rx.search(name):
            return group
    return None


def is_excluded(rules, name):
    """Упаковка и сервис: «ПАКЕТ ЛЕНТА СРЕДНИЙ МАЙКА 12КГ» — это
    грузоподъёмность пакета, а не вес товара. До фасовки такие не доходят."""
    return bool(rules.excluded.search(name))


def find_brand(rules, name):
    """Касса режет бренд до 4–8 символов. Словарь пополняется пользователем."""
    for brand, rx in rules.brands:
        if rx.search(name):
            return brand["brand"]
    return "—"


def fractional_names(rules, receipts, food_shops):
    """Названия, у которых доля покупок с дробным количеством ≥ 0,5.

    Это весовой товар, даже если слова «ВЕС» в названии нет: БАНАНЫ, ЛУК
    РЕПЧАТЫЙ, ЛИМОНЫ. Считается по ВСЕЙ истории названия, а не по одной
    покупке: разовое дробное количество бывает и у штучного товара (SPEC §5).
    """
    seen = defaultdict(list)
    for r in receipts:
        canon, _tier = classify_shop(rules, r.shop)
        if canon in food_shops:
            for it in r.items:
                seen[fold(rules, clean(rules, it.name))].append(it.qty)
    return {
        name: sum(1 for q in qs if abs(q - round(q)) > 1e-9) / len(qs)
        for name, qs in seen.items()
        if qs and sum(1 for q in qs if abs(q - round(q)) > 1e-9) / len(qs) >= 0.5
    }


def weighted_reason(rules, name, fractional):
    """Весовой или нет — и почему. Проверяется ДО извлечения фасовки.

    Порядок критичен (docs/DECISIONS.md Р-3): 415 весовых позиций содержат
    «1КГ» в тексте названия, и при обратном порядке «БАНАНЫ 1КГ» стал бы
    штучным SKU с фасовкой 1 кг и брендом «—». У весового бренд и штучная
    фасовка неприменимы, а не неизвестны (SPEC §5).
    """
    if rules.weighted.search(name):
        return "весовой"
    if name in fractional:
        return "весовой (дробное кол-во)"
    return None


def extract_pack(rules, name, group_id):
    """→ (единица, значение, чем определено) либо None.

    Порядок правил — часть логики (SPEC §5):
      явная единица → литраж у напитков → число перед ШТ → голое число в конце.
    Габариты и жирность вырезаются заранее: «СМЕТАНА 25%300Г» — фасовка 300 г,
    а 25 это жирность.
    """
    n = RE_DIMS.sub(" ", name)
    n = RE_FAT.sub(" ", n)

    for _label, rx, unit, factor in PACK_RULES:
        m = rx.search(n)
        if m:
            try:
                value = float(m.group(1).replace(",", "."))
            except ValueError:
                return None
            return (unit, value * factor, "явная единица")

    if group_id in DRINK_GROUPS:
        m = RE_BARE_LITRES.search(n)
        if m:
            return ("l", float(m.group(1).replace(",", ".")), "литраж без единицы")

    m = RE_PIECES.search(n)
    if m:
        value = float(m.group(1))
        lo, hi = PIECES_RANGE
        if lo <= value <= hi:
            return ("pcs", value, "число перед ШТ")

    m = RE_BARE_NUMBER.search(n)
    if m:
        value = float(m.group(1))
        if group_id in LIQUID_GROUPS:
            return ("l", value / 1000, "голое число (мл)")
        return ("kg", value / 1000, "голое число (г)")

    return None


def extract_fat(name):
    """Жирность/сорт — различитель SKU, но не фасовка."""
    m = RE_FAT.search(name)
    return m.group(1) if m else None
