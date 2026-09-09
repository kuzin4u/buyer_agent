"""Текстовый отчёт прогона — то, что печатает validate.py.

Отделён от печати, чтобы эталонный тест сравнивал строки без запуска процесса.
Формат вывода — контрольный артефакт (docs/BASELINE.txt): любое изменение здесь
обязано быть осознанным и объяснённым.
"""

import statistics

from .diagnostics import comparability, coverage

WIDTH = 62
TIER_ORDER = ("food_core", "food_candidate", "non_food", "unknown")


def _money(text):
    """Разряды пробелом, как в эталоне."""
    return text.replace(",", " ")


def render(run):
    """→ строка отчёта, без завершающего перевода строки."""
    out = []
    p = out.append
    cov = coverage(run)

    p("=" * WIDTH)
    p("1. КЛАССИФИКАЦИЯ ПРОДАВЦОВ")
    p("=" * WIDTH)
    p(_money(f"всего чеков: {run.receipts_count}, "
             f"сумма: {run.receipts_sum:,.0f} ₽"))
    for tier in TIER_ORDER:
        n, s = run.tier_stat.get(tier, (0, 0.0))
        p(_money(f"  {tier:16} {n:5} чеков  {s:12,.0f} ₽"))

    p("")
    p("Продуктовое ядро по магазинам:")
    for shop, (n, s) in sorted(run.shop_stat.items(), key=lambda x: -x[1][1]):
        p(_money(f"  {shop:20} {n:5} чеков  {s:11,.0f} ₽"))

    core_n, core_s = run.tier_stat.get("food_core", (0, 0.0))
    ok = (core_n == 839) and (abs(core_s - 3694000) < 5000)
    p("")
    p(_money(f"  контроль ТЗ (839 чеков / ~3 694 тыс ₽): "
             f"{'СОВПАЛО' if ok else 'РАСХОЖДЕНИЕ'}  → {core_n} / {core_s:,.0f} ₽"))

    p("")
    p("=" * WIDTH)
    p("2. ПРОДАВЕЦ НЕ ОПРЕДЕЛЁН (правило unknown_resolution)")
    p("=" * WIDTH)
    unknown_sum = sum(r.total for r in run.unknown_receipts)
    p(_money(f"  продавец не определён: {len(run.unknown_receipts)} чеков, "
             f"сумма {unknown_sum:,.0f} ₽"))
    p(_money(f"  признаны продуктовыми (доля продуктов ≥ "
             f"{run.rules.food_share_threshold}): "
             f"{run.unknown_food_count} чеков, {run.unknown_food_sum:,.0f} ₽"))

    p("")
    p("=" * WIDTH)
    p("3. ПОЗИЦИИ: категоризация, фасовка, бренд")
    p("=" * WIDTH)
    p(f"  позиций в продуктовых чеках: {cov.total}")
    p(f"  исключено как упаковка/сервис: {cov.excluded} "
      f"({cov.excluded / cov.total:.1%})")
    p(f"  отнесено к товарной группе: {cov.matched} "
      f"({cov.matched / cov.analysable:.1%} от анализируемых)")
    p(f"  из них весовых (цена уже ₽/кг): {cov.weighted}")
    p(f"  фасовка или вес определены: {cov.with_pack} "
      f"({cov.with_pack / cov.analysable:.1%} от анализируемых)")
    p(f"  бренд распознан: {cov.brand_hit} из {cov.unit_items} штучных")
    p("  чем определена фасовка:")
    for source, n in cov.by_source.most_common():
        p(f"    {n:5}  {source}")
    p(f"  уникальных SKU (штучных): {len(cov.unit_skus)}")

    # Метрика сравнимости: штучные и весовые РАЗДЕЛЬНО (docs/DECISIONS.md Р-3).
    cmp_ = comparability(run, cov)
    p(f"  сравнимы между магазинами (≥2 магазина по ≥{cmp_['min_observations']} набл.):")
    p(f"    штучные: {cmp_['unit'][0]:4} из {cmp_['unit'][1]}")
    p(f"    весовые: {cmp_['bulk'][0]:4} из {cmp_['bulk'][1]}")

    p("")
    p("  Топ-15 нераспознанных названий — очередь на пополнение categories.json:")
    for name, n in cov.unmatched.most_common(15):
        p(f"    {n:4}  {name}")

    p("")
    p("=" * WIDTH)
    p("4. КОНТРОЛЬНЫЙ ПРОГОН: сметана")
    p("=" * WIDTH)
    smetana = {k: v for k, v in cov.unit_skus.items() if k.group == "smetana"}
    p(f"  SKU сметаны: {len(smetana)}, "
      f"покупок: {sum(len(v) for v in smetana.values())}")
    p("  (демо-референс: 25 SKU, 199 покупок — расхождение ожидаемо,")
    p("   демо считало по одному магазину и другому набору правил)")
    for key, items in sorted(smetana.items(), key=lambda x: -len(x[1]))[:8]:
        med = statistics.median(i.unit_price for i in items)
        p(f"    {len(items):3}×  {key.parts[0]} · {key.parts[1]}% · "
          f"{key.parts[2]:8}  медиана {med:7.0f} ₽/ед")

    return "\n".join(out)
