"""Шов горизонта (SPEC §8.10).

«Если горизонт свободный параметр, ничто не помешает выставить агенту
покупателя продавцовский, и останется одно обещание так не делать. Если он
производная от того, откуда позиция, — злоупотребление невозможно.»
"""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from agent.matching import Horizon, Mode, check_horizon, OWN_HISTORY_MODE  # noqa: E402


class HorizonTest(unittest.TestCase):
    def test_derived_from_catalog_mode(self):
        self.assertEqual(Horizon.of("repeatable").mode, Mode.REPEATABLE)
        self.assertEqual(Horizon.of("one_off").mode, Mode.ONE_OFF)

    def test_substitution_only_in_repeatable_environment(self):
        """В разовой среде позицию показываем, но замену привычного не предлагаем."""
        self.assertTrue(Horizon.of("repeatable").allows_substitution)
        self.assertFalse(Horizon.of("one_off").allows_substitution)

    def test_foreign_horizon_rejected(self):
        """Горизонт от чужого каталога не проходит — запрет конструктивный."""
        with self.assertRaises(ValueError):
            check_horizon("one_off", Horizon.of("repeatable"))
        with self.assertRaises(ValueError):
            check_horizon("repeatable", Horizon.of("one_off"))

    def test_own_catalog_is_repeatable(self):
        """Первая версия закупщика: доступен только собственный каталог покупок."""
        self.assertIs(OWN_HISTORY_MODE, Mode.REPEATABLE)
        check_horizon(OWN_HISTORY_MODE.value, Horizon.of(OWN_HISTORY_MODE.value))


if __name__ == "__main__":
    unittest.main()
