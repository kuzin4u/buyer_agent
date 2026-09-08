#!/usr/bin/env python3
"""
Проверка конфигов на реальном датасете.

Запуск:  python3 validate.py

Ничего не строит и не пишет — только применяет config/*.json к data/receipts.json
и печатает покрытие. Нужен, чтобы после правки любого конфига было видно,
что стало лучше, а что сломалось. Контрольные цифры — в docs/DATASET.md.
"""

import json
import os
import re
import statistics
from collections import Counter, defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    with open(os.path.join(BASE, name), encoding="utf-8") as f:
        return json.load(f)


SHOPS = load("config/shops.json")
NORM = load("config/normalization.json")
CATS = load("config/categories.json")
BRANDS = load("config/brands.json")
REC = load("data/receipts.json")

HOMO = str.maketrans({k: v for k, v in NORM["homoglyphs"].items()
                      if not k.startswith("_")})
RE_CYR = re.compile(r"[А-Яа-яЁё]")
RE_WORD = re.compile(r"[A-Za-zА-Яа-яЁё]+")


def fix_homoglyphs(s):
    """Пословно: латинский гомоглиф чинится только внутри слова, где есть
    кириллица. Иначе PATERRA превращается в РАТЕRRА."""
    return RE_WORD.sub(
        lambda m: m.group(0).translate(HOMO) if RE_CYR.search(m.group(0)) else m.group(0),
        s)
RE_EXCL = re.compile(NORM["excluded_items"]["match"])
RE_WEIGHTED = re.compile(NORM["weighted_goods"]["match"])
RE_DIMS = re.compile(r"\d+\s*[ХX]\s*\d+\s*(СМ)?")
RE_FAT = re.compile(r"(\d+[.,]?\d*)\s*%")

SHOP_RULES = [(r, re.compile(r["match"])) for r in SHOPS["rules"]]
CAT_RULES = [(g, re.compile(g["match"])) for g in CATS["groups"]]
BRAND_RULES = [(b, re.compile(b["match"])) for b in BRANDS["brands"]]

PACK_RULES = [
    ("кг", re.compile(r"(\d+[.,]?\d*)\s*КГ\b"), lambda v: ("kg", v)),
    ("г", re.compile(r"(\d+[.,]?\d*)\s*Г(?![А-Я])"), lambda v: ("kg", v / 1000)),
    ("л", re.compile(r"(\d+[.,]?\d*)\s*Л(?![А-Я])"), lambda v: ("l", v)),
    ("мл", re.compile(r"(\d+[.,]?\d*)\s*МЛ\b"), lambda v: ("l", v / 1000)),
]
DRINKS = {"voda", "sok", "pivo", "vino", "krepkiy"}
RE_BARE_L = re.compile(r"\b([01],\d{1,2})\s*$")


def clean(name):
    return fix_homoglyphs(name).upper().strip()


def classify_shop(raw):
    if not raw or raw.strip() in ("", "не указан"):
        return None, "unknown"
    up = clean(raw)
    for rule, rx in SHOP_RULES:
        if rx.search(up):
            return rule["canonical"], rule["tier"]
    return raw, "unknown"


def classify_item(name):
    for g, rx in CAT_RULES:
        if rx.search(name):
            return g
    return None


def find_brand(name):
    for b, rx in BRAND_RULES:
        if rx.search(name):
            return b["brand"]
    return "—"


def extract_pack(name, group_id):
    """Возвращает (unit, value) в кг или литрах, либо None."""
    n = RE_DIMS.sub(" ", name)
    n = RE_FAT.sub(" ", n)  # жирность не фасовка
    for _, rx, conv in PACK_RULES:
        m = rx.search(n)
        if m:
            try:
                return conv(float(m.group(1).replace(",", ".")))
            except ValueError:
                return None
    if group_id in DRINKS:
        m = RE_BARE_L.search(n)
        if m:
            return ("l", float(m.group(1).replace(",", ".")))
    return None


def main():
    include_candidates = os.environ.get("INCLUDE_CANDIDATES") == "1"
    food_tiers = {"food_core"} | ({"food_candidate"} if include_candidates else set())

    shop_stat = defaultdict(lambda: [0, 0.0])
    tier_stat = defaultdict(lambda: [0, 0.0])
    food_receipts = []
    unknown_receipts = []

    for r in REC:
        canon, tier = classify_shop(r["shop"])
        tier_stat[tier][0] += 1
        tier_stat[tier][1] += r["total"]
        if tier in food_tiers:
            shop_stat[canon][0] += 1
            shop_stat[canon][1] += r["total"]
            food_receipts.append((canon, r))
        elif tier == "unknown":
            unknown_receipts.append(r)

    print("=" * 62)
    print("1. КЛАССИФИКАЦИЯ ПРОДАВЦОВ")
    print("=" * 62)
    print(f"всего чеков: {len(REC)}, сумма: {sum(r['total'] for r in REC):,.0f} ₽".replace(",", " "))
    for t in ("food_core", "food_candidate", "non_food", "unknown"):
        n, s = tier_stat[t]
        print(f"  {t:16} {n:5} чеков  {s:12,.0f} ₽".replace(",", " "))
    print("\nПродуктовое ядро по магазинам:")
    for k, (n, s) in sorted(shop_stat.items(), key=lambda x: -x[1][1]):
        print(f"  {k:20} {n:5} чеков  {s:11,.0f} ₽".replace(",", " "))
    core_n = tier_stat["food_core"][0]
    core_s = tier_stat["food_core"][1]
    ok = (core_n == 839) and (abs(core_s - 3694000) < 5000)
    print(f"\n  контроль ТЗ (839 чеков / ~3 694 тыс ₽): {'СОВПАЛО' if ok else 'РАСХОЖДЕНИЕ'}"
          f"  → {core_n} / {core_s:,.0f} ₽".replace(",", " "))

    print()
    print("=" * 62)
    print("2. ЧЕКИ БЕЗ ПРОДАВЦА (правило unknown_resolution)")
    print("=" * 62)
    thr = SHOPS["unknown_resolution"]["food_share_threshold"]
    passed = passed_sum = 0
    for r in unknown_receipts:
        tot = sum(i["s"] for i in r["items"]) or 1
        food = sum(i["s"] for i in r["items"] if classify_item(clean(i["n"])))
        if food / tot >= thr:
            passed += 1
            passed_sum += r["total"]
    print(f"  чеков без продавца: {len(unknown_receipts)}, "
          f"сумма {sum(r['total'] for r in unknown_receipts):,.0f} ₽".replace(",", " "))
    print(f"  признаны продуктовыми (доля продуктов ≥ {thr}): "
          f"{passed} чеков, {passed_sum:,.0f} ₽".replace(",", " "))

    print()
    print("=" * 62)
    print("3. ПОЗИЦИИ: категоризация, фасовка, бренд")
    print("=" * 62)
    total = excluded = matched = with_pack = weighted = 0
    unmatched_names = Counter()
    brand_hit = 0
    sku = defaultdict(list)

    for canon, r in food_receipts:
        for it in r["items"]:
            n = clean(it["n"])
            total += 1
            if RE_EXCL.search(n):
                excluded += 1
                continue
            g = classify_item(n)
            if not g:
                unmatched_names[n] += 1
                continue
            matched += 1
            if RE_WEIGHTED.search(n):
                weighted += 1
                with_pack += 1
                continue
            p = extract_pack(n, g["id"])
            if p:
                with_pack += 1
                brand = find_brand(n)
                if brand != "—":
                    brand_hit += 1
                fat = RE_FAT.search(n)
                key = (g["id"], brand, fat.group(1) if fat else "—",
                       f"{p[1]:g}{'кг' if p[0] == 'kg' else 'л'}")
                sku[key].append((r["dt"][:4], it["p"] / (p[1] or 1)))

    analysable = total - excluded
    print(f"  позиций в продуктовых чеках: {total}")
    print(f"  исключено как упаковка/сервис: {excluded} ({excluded/total:.1%})")
    print(f"  отнесено к товарной группе: {matched} ({matched/analysable:.1%} от анализируемых)")
    print(f"  из них весовых (цена уже ₽/кг): {weighted}")
    print(f"  фасовка или вес определены: {with_pack} ({with_pack/analysable:.1%} от анализируемых)")
    print(f"  бренд распознан: {brand_hit} из {with_pack - weighted} штучных")
    print(f"  уникальных SKU: {len(sku)}")

    print("\n  Топ-15 нераспознанных названий — очередь на пополнение categories.json:")
    for n, k in unmatched_names.most_common(15):
        print(f"    {k:4}  {n}")

    print()
    print("=" * 62)
    print("4. КОНТРОЛЬНЫЙ ПРОГОН: сметана")
    print("=" * 62)
    sm = {k: v for k, v in sku.items() if k[0] == "smetana"}
    print(f"  SKU сметаны: {len(sm)}, покупок: {sum(len(v) for v in sm.values())}")
    print(f"  (демо-референс: 25 SKU, 199 покупок — расхождение ожидаемо,")
    print("   демо считало по одному магазину и другому набору правил)")
    for k, v in sorted(sm.items(), key=lambda x: -len(x[1]))[:8]:
        med = statistics.median(p for _, p in v)
        print(f"    {len(v):3}×  {k[1]} · {k[2]}% · {k[3]:8}  медиана {med:7.0f} ₽/ед")


if __name__ == "__main__":
    main()
