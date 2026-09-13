"""8.3 Корзина под бюджет и каталог подбора (SPEC §8.3, §8.10).

Главное, что здесь проверяется, — не арифметика, а порядок рычагов. Замена
внутри группы оставляет потребность закрытой, выброс — нет, поэтому замена
всегда раньше. И ни один из рычагов не должен предлагать того, чего из чеков не
следует: «лук вместо капусты» формально дешевле, но это не замена.
"""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fixture  # noqa: E402

from agent.profile import build, for_period                          # noqa: E402
from agent.settings import Settings                                 # noqa: E402
from agent.matching import (Catalog, Horizon, Mode, Offer, candidates,  # noqa: E402
                            fit, from_history, substitutable)
from agent.matching.budget import MIN_GAIN_SHARE                    # noqa: E402


def as_mode(catalog, mode):
    """Тот же каталог, но позиции пришли из другого места."""
    return Catalog(offers={k: Offer(key=o.key, unit_price=o.unit_price,
                                    observations=o.observations, mode=mode)
                           for k, o in catalog.offers.items()},
                   window=catalog.window)


class CatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.history = fixture.history()
        cls.catalog = from_history(cls.history)

    def test_own_purchases_are_a_repeatable_catalog(self):
        """Своя история — повторяемая среда: сделка состоялась (SPEC §8.10)."""
        self.assertIs(self.catalog.mode, Mode.REPEATABLE)
        self.assertTrue(all(o.mode is Mode.REPEATABLE
                            for o in self.catalog.offers.values()))

    def test_window_is_the_tail_of_history(self):
        """Цена 2017 года не является предложением в 2026-м (Р-21)."""
        start, end = self.catalog.window
        self.assertEqual(end, self.history.span()[1][:10])
        self.assertLess(start, end)
        for offer in self.catalog.offers.values():
            self.assertGreaterEqual(offer.first_ts[:10], start)

    def test_only_comparable_keys_enter(self):
        """Весовые и смешанные ключи в каталог не попадают вовсе."""
        for key, offer in self.catalog.offers.items():
            self.assertEqual(key.kind, "unit")
            self.assertFalse(key.blended)
            self.assertGreaterEqual(offer.observations, 3)

    def test_bulk_and_blended_are_not_substitutable(self):
        bulk = next(e.key for e in self.history.events if e.key.kind == "bulk")
        blended = next(e.key for e in self.history.events if e.key.blended)
        self.assertFalse(substitutable(bulk))
        self.assertFalse(substitutable(blended))
        self.assertTrue(substitutable(next(iter(self.catalog.offers))))

    def test_alternatives_stay_inside_group_and_unit(self):
        """Сложить ₽/кг с ₽/шт нельзя, значит и сравнить нельзя (SPEC §5)."""
        for key in self.catalog.offers:
            alts = self.catalog.alternatives(key)
            self.assertIn(key, [o.key for o in alts])
            for offer in alts:
                self.assertEqual(offer.group, key.group)
                self.assertEqual(offer.unit, key.unit)
            self.assertEqual(list(alts), sorted(alts, key=lambda o: (o.unit_price,
                                                                    o.label)))

    def test_mixed_catalog_has_no_common_mode(self):
        """Каталог Системы рядом с каталогом рынка общего горизонта не имеет."""
        keys = list(self.catalog.offers)[:2]
        mixed = Catalog(offers={
            keys[0]: Offer(key=keys[0], unit_price=10, observations=3,
                           mode=Mode.REPEATABLE),
            keys[1]: Offer(key=keys[1], unit_price=10, observations=3,
                           mode=Mode.ONE_OFF)})
        self.assertIsNone(mixed.mode)

    def test_candidates_are_cheaper_by_at_least_the_threshold(self):
        for key in self.catalog.offers:
            here = self.catalog.price(key)
            for offer in candidates(self.catalog, key):
                self.assertGreaterEqual((here - offer.unit_price) / here,
                                        MIN_GAIN_SHARE)

    def test_candidates_refuses_bulk_source(self):
        bulk = next(e.key for e in self.history.events if e.key.kind == "bulk")
        self.assertEqual(candidates(self.catalog, bulk), ())


class BudgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.history = fixture.history()
        cls.profile = fixture.profile()
        cls.catalog = from_history(cls.history)
        cls.settings = Settings()
        cls.week = for_period(cls.profile, "week", cls.settings).total
        cls.month = for_period(cls.profile, "month", cls.settings).total

    def fit(self, budget, period="week", **kw):
        return fit(self.profile, self.catalog, budget, period=period,
                   settings=kw.pop("settings", self.settings), **kw)

    def test_roomy_budget_changes_nothing(self):
        f = self.fit(self.week * 2)
        self.assertEqual(f.swaps, ())
        self.assertEqual(f.dropped, ())
        self.assertAlmostEqual(f.total, f.total_before)
        self.assertTrue(f.fits)

    def test_swap_comes_before_dropping(self):
        """Главный рычаг — замена: потребность остаётся закрытой (§8.3)."""
        f = self.fit(self.month - 120, period="month")
        self.assertTrue(f.swaps)
        self.assertEqual(f.dropped, ())
        self.assertTrue(f.fits)

    def test_swap_keeps_the_need_in_the_basket(self):
        f = self.fit(self.month - 120, period="month")
        groups = {line.group for line in f.lines}
        for swap in f.swaps:
            self.assertIn(swap.from_key.group, groups)
            self.assertEqual(swap.from_key.group, swap.to_key.group)
            self.assertEqual(swap.from_key.unit, swap.to_key.unit)
            self.assertLess(swap.to_price, swap.from_price)

    def test_swapped_line_shows_both_sides(self):
        """§8.3 требует показывать, чем именно заменили."""
        f = self.fit(self.month - 120, period="month")
        line = next(x for x in f.lines if x.swap)
        self.assertNotEqual(line.was, line.label)
        self.assertEqual(line.label, line.swap.to_key.label)
        self.assertAlmostEqual(line.amount, line.line.amount - line.swap.saving)

    def test_totals_add_up(self):
        for budget in (self.week, self.week - 5, self.week * 0.8, self.week * 0.3):
            f = self.fit(budget)
            self.assertAlmostEqual(f.total, sum(x.amount for x in f.lines))
            self.assertAlmostEqual(f.total_before - f.total,
                                   f.saved_by_swaps + f.saved_by_drops)
            self.assertEqual(f.considered, len(f.lines) + len(f.dropped))

    def test_marginal_swap_only_when_it_saves_a_group(self):
        """Мелкая замена оправдана лишь тем, что иначе выбросили бы группу.

        При недостижимо малом бюджете группы всё равно выбрасываются, и менять
        привычку ради 10 ₽ незачем — таких замен быть не должно.
        """
        deep = self.fit(self.month * 0.6, period="month")
        self.assertTrue(deep.dropped)
        self.assertFalse([s for s in deep.swaps if s.marginal])

        # Разрыв заведомо меньше любой замены, которая стоила бы сама по себе:
        # закрыть его может только мелкая, и тогда она оправдана — иначе
        # пришлось бы выбросить группу целиком.
        tight = self.fit(self.week - 5)
        marginal = [s for s in tight.swaps if s.marginal]
        self.assertTrue(marginal)
        self.assertEqual(tight.dropped, ())
        self.assertTrue(tight.fits)

    def test_worthwhile_swap_clears_the_threshold(self):
        f = self.fit(self.month - 120, period="month")
        for swap in f.swaps:
            if swap.worthwhile:
                self.assertGreaterEqual(swap.gain_share, MIN_GAIN_SHARE)
            else:
                self.assertLess(swap.gain_share, MIN_GAIN_SHARE)

    def test_drops_start_from_the_least_regular(self):
        f = self.fit(self.week * 0.6)
        self.assertTrue(f.dropped)
        rates = [self.profile.groups[l.group].per_month for l in f.dropped]
        kept = [self.profile.groups[l.group].per_month for l in f.lines
                if l.reason != "обязательная" and l.group in self.profile.groups]
        self.assertLessEqual(max(rates), min(kept) + 1e-9)

    def test_required_group_is_never_dropped(self):
        settings = Settings(required_groups=("syr", "vino"))
        profile = build(self.history, settings)
        f = fit(profile, self.catalog, 500, period="month", settings=settings)
        groups = {line.group for line in f.lines}
        self.assertTrue({"syr", "vino"} <= groups)
        self.assertFalse({"syr", "vino"} & {l.group for l in f.dropped})

    def test_unreachable_budget_is_reported_not_hidden(self):
        settings = Settings(required_groups=("syr", "vino"))
        profile = build(self.history, settings)
        f = fit(profile, self.catalog, 100, period="month", settings=settings)
        self.assertFalse(f.fits)
        self.assertLess(f.slack, 0)
        self.assertTrue(all(l.reason == "обязательная" for l in f.lines))

    def test_bulk_line_is_never_swapped_and_says_why(self):
        """«Лук вместо капусты» формально внутри группы, но это другой товар."""
        f = self.fit(self.week * 0.9)
        bulk = [x for x in f.lines
                if x.line.typical_key and x.line.typical_key.kind == "bulk"]
        self.assertTrue(bulk)
        for line in bulk:
            self.assertIsNone(line.swap)
            self.assertIn("весовой", line.blocked)

    def test_blended_line_is_never_swapped_and_says_why(self):
        f = self.fit(self.week * 0.9)
        blended = [x for x in f.lines
                   if x.line.typical_key and x.line.typical_key.blended]
        self.assertTrue(blended)
        for line in blended:
            self.assertIsNone(line.swap)
            self.assertIn("марка", line.blocked)

    def test_coverage_is_reported(self):
        f = self.fit(self.month, period="month")
        self.assertLessEqual(f.comparable, f.considered)
        self.assertLessEqual(f.substitutable, f.comparable)
        self.assertGreater(f.considered, f.comparable)   # покрытие неполное, и это видно

    def test_bad_budget_and_period_are_rejected(self):
        for bad in (None, 0, -100):
            with self.assertRaises(ValueError):
                self.fit(bad)
        with self.assertRaises(ValueError):
            self.fit(1000, period="quarter")


class HorizonInMatchingTest(unittest.TestCase):
    """Горизонт — производная режима позиции, и это видно по поведению подбора."""

    @classmethod
    def setUpClass(cls):
        cls.profile = fixture.profile()
        cls.catalog = from_history(fixture.history())
        cls.month = for_period(cls.profile, "month").total

    def test_one_off_environment_offers_no_substitution(self):
        """В разовой среде позицию показываем, но замену привычного — нет."""
        f = fit(self.profile, as_mode(self.catalog, Mode.ONE_OFF),
                self.month - 120, period="month")
        self.assertFalse(f.substitution_allowed)
        self.assertEqual(f.swaps, ())
        self.assertTrue(f.dropped)          # уложились только выбросом
        self.assertTrue(all("разовая среда" in l.blocked for l in f.lines))

    def test_repeatable_environment_does_substitute(self):
        f = fit(self.profile, self.catalog, self.month - 120, period="month")
        self.assertTrue(f.substitution_allowed)
        self.assertTrue(f.swaps)

    def test_horizon_is_derived_not_passed(self):
        f = fit(self.profile, self.catalog, self.month, period="month")
        self.assertEqual(f.horizon, Horizon.of(self.catalog.mode.value))

    def test_foreign_horizon_is_rejected(self):
        """Агенту покупателя нельзя выставить продавцовский горизонт."""
        with self.assertRaises(ValueError):
            fit(self.profile, self.catalog, self.month, period="month",
                horizon=Horizon.of("one_off"))
        with self.assertRaises(ValueError):
            fit(self.profile, as_mode(self.catalog, Mode.ONE_OFF), self.month,
                period="month", horizon=Horizon.of("repeatable"))

    def test_own_horizon_passes(self):
        f = fit(self.profile, self.catalog, self.month, period="month",
                horizon=Horizon.of("repeatable"))
        self.assertIs(f.horizon.mode, Mode.REPEATABLE)

    def test_mixed_catalog_refuses_a_forced_horizon(self):
        keys = list(self.catalog.offers)[:2]
        mixed = Catalog(offers={
            keys[0]: Offer(key=keys[0], unit_price=10, observations=3,
                           mode=Mode.REPEATABLE),
            keys[1]: Offer(key=keys[1], unit_price=10, observations=3,
                           mode=Mode.ONE_OFF)})
        with self.assertRaises(ValueError):
            fit(self.profile, mixed, 1000, horizon=Horizon.of("repeatable"))
        # без горизонта подбор работает, но осторожно: замен не предлагает
        f = fit(self.profile, mixed, 1000)
        self.assertFalse(f.substitution_allowed)


if __name__ == "__main__":
    unittest.main()
