"""Слой ввода: боевой ФНС-JSON и пакетный датасет сходятся в одну модель.

SPEC §3.2: «Парсер боевого ФНС-JSON и загрузчик этого датасета должны сходиться
в одну внутреннюю модель. Пиши слой ввода так, чтобы источник подменялся.»
"""

import json
import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from agent.config import dataset_path                                   # noqa: E402
from agent.adapters.receipts_fns import load_dataset, load_receipts     # noqa: E402
from agent.adapters.receipts_fns.loader import parse_fns_export         # noqa: E402


def as_fns(receipt):
    """Тот же чек в форме боевой выгрузки: копейки и вложенность ticket/document."""
    return {
        "ticket": {"document": {"receipt": {
            "dateTime": receipt.dt,
            "user": receipt.shop,
            "retailPlaceAddress": receipt.addr,
            "totalSum": round(receipt.total * 100),
            "items": [
                {"name": i.name, "price": round(i.price * 100),
                 "quantity": i.qty, "sum": round(i.total * 100)}
                for i in receipt.items
            ],
        }}}
    }


class LoaderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(dataset_path(BASE))

    def test_dataset_shape(self):
        self.assertEqual(len(self.dataset), 1744)
        self.assertEqual(sum(len(r.items) for r in self.dataset), 19056)
        self.assertEqual(round(sum(r.total for r in self.dataset)), 5583884)

    def test_fns_export_converges_with_dataset(self):
        """Копейки переводятся в рубли, поля ложатся на ту же модель."""
        sample = self.dataset[:50]
        parsed = parse_fns_export([as_fns(r) for r in sample])
        self.assertEqual(len(parsed), len(sample))
        for got, want in zip(parsed, sample):
            self.assertEqual(got.dt, want.dt)
            self.assertEqual(got.shop, want.shop)
            self.assertAlmostEqual(got.total, want.total, places=2)
            self.assertEqual(len(got.items), len(want.items))
            for a, b in zip(got.items, want.items):
                self.assertEqual(a.name, b.name)
                self.assertAlmostEqual(a.price, b.price, places=2)
                self.assertAlmostEqual(a.qty, b.qty, places=6)
                self.assertAlmostEqual(a.total, b.total, places=2)

    def test_format_detected_automatically(self):
        """load_receipts различает форматы по содержимому, а не по имени файла."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "fns.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump([as_fns(r) for r in self.dataset[:5]], f, ensure_ascii=False)
            parsed = load_receipts(path)
        self.assertEqual(len(parsed), 5)
        self.assertAlmostEqual(parsed[0].total, self.dataset[0].total, places=2)

    def test_empty_seller_survives_parsing(self):
        """Пустой продавец — не ошибка: 196 таких чеков, 336 тыс ₽ (SPEC §7)."""
        parsed = parse_fns_export([{"ticket": {"document": {"receipt": {
            "dateTime": "2019-05-01T10:00:00", "user": None,
            "retailPlaceAddress": None, "totalSum": 12345,
            "items": [{"name": "МОЛОКО", "price": 5000, "quantity": 2, "sum": 10000}],
        }}}}])
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].shop, "")
        self.assertAlmostEqual(parsed[0].total, 123.45, places=2)


if __name__ == "__main__":
    unittest.main()
