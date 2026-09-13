"""Слой ввода: боевой ФНС-JSON и пакетный датасет сходятся в одну модель.

SPEC §3.2: «Парсер боевого ФНС-JSON и загрузчик этого датасета должны сходиться
в одну внутреннюю модель. Пиши слой ввода так, чтобы источник подменялся.»
"""

import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from agent.config import dataset_path                                   # noqa: E402
from agent.adapters.receipts_fns import load_dataset, load_receipts     # noqa: E402
from agent.adapters.receipts_fns.loader import parse_fns_export         # noqa: E402


def as_fns(receipt, fn=None, fd=None, fpd=None):
    """Тот же чек в форме боевой выгрузки: копейки и вложенность ticket/document."""
    body = {
        "dateTime": receipt.dt,
        "user": receipt.shop,
        "retailPlaceAddress": receipt.addr,
        "totalSum": round(receipt.total * 100),
        "items": [
            {"name": i.name, "price": round(i.price * 100),
             "quantity": i.qty, "sum": round(i.total * 100)}
            for i in receipt.items
        ],
    }
    if fn is not None:
        body["fiscalDriveNumber"] = fn
    if fd is not None:
        body["fiscalDocumentNumber"] = fd
    if fpd is not None:
        body["fiscalSign"] = fpd
    return {"ticket": {"document": {"receipt": body}}}


class IdentityTest(unittest.TestCase):
    """Тождество чека: чем он равен самому себе в следующей выгрузке.

    Выгрузки перекрываются по построению — человек выгружает «всё», а не
    «новое». Без тождества вторая выгрузка удваивает период, а восстановить
    реквизиты из уже разобранного корпуса нечем.
    """

    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(dataset_path(BASE))

    def parse_one(self, receipt, **fiscal):
        return parse_fns_export([as_fns(receipt, **fiscal)])[0]

    def test_fiscal_id_needs_all_three_requisites(self):
        """Неполный набор — это не частичное тождество, а его отсутствие.

        ФД уникален только внутри своего накопителя, а ФПД подтверждает, что
        документ не подменён: по двум реквизитам из трёх склеивать нельзя.
        """
        sample = self.dataset[0]
        full = self.parse_one(sample, fn="9287440300123456", fd=45678,
                              fpd=1234567890)
        self.assertEqual(full.fiscal_id, "9287440300123456:45678:1234567890")
        self.assertEqual(full.identity[0], "fiscal")

        for missing in ("fn", "fd", "fpd"):
            kw = {"fn": "9287440300123456", "fd": 45678, "fpd": 1234567890}
            kw.pop(missing)
            partial = self.parse_one(sample, **kw)
            self.assertIsNone(partial.fiscal_id,
                              f"без {missing} тождество всё равно собралось")
            self.assertEqual(partial.identity[0], "digest")

    def test_requisites_survive_being_numbers(self):
        """ФН приходит строкой, ФД и ФПД — числами; ключ обязан совпасть."""
        sample = self.dataset[0]
        as_text = self.parse_one(sample, fn="928744", fd="45678", fpd="1234567890")
        as_numbers = self.parse_one(sample, fn="928744", fd=45678, fpd=1234567890)
        self.assertEqual(as_text.fiscal_id, as_numbers.fiscal_id)

    def test_dataset_has_no_requisites_and_says_so(self):
        """У эталонного датасета реквизитов нет: он получен преобразованием.

        Это не дефект датасета, а факт о нём, и он должен быть виден: склейка
        такого чека с боевой выгрузкой пойдёт по слабому ключу.
        """
        self.assertTrue(all(r.fiscal_id is None for r in self.dataset))
        self.assertTrue(all(r.identity[0] == "digest" for r in self.dataset))

    def test_content_digest_separates_all_1744_receipts(self):
        """Отпечаток содержимого различает весь эталон — значит он рабочий ключ.

        Три чека в датасете делят отметку времени, и ни один не совпадает по
        содержимому: дата сама по себе ключом быть не может, а отпечаток может.
        """
        self.assertEqual(len({r.digest for r in self.dataset}), len(self.dataset))
        self.assertEqual(len({r.identity for r in self.dataset}), len(self.dataset))

    def test_digest_does_not_depend_on_the_format_it_travelled_through(self):
        """Один чек из двух источников даёт ОДИН отпечаток.

        Иначе слабый ключ бесполезен там, где он единственный: датасет отдаёт
        рубли, выгрузка — копейки, и если отпечаток зависит от этого, тот же
        чек придёт в корпус дважды.
        """
        for receipt in self.dataset[:200]:
            through_fns = self.parse_one(receipt)
            self.assertEqual(through_fns.digest, receipt.digest,
                             f"{receipt.dt}: отпечаток изменился в пути")

    def test_digest_changes_when_anything_in_the_receipt_changes(self):
        """Иначе склейка отбросит чек, который на самом деле другой."""
        base = self.dataset[0]
        first = base.items[0]
        variants = {
            "дата": replace(base, dt="2020-01-01T00:00:00"),
            "магазин": replace(base, shop=base.shop + " "),
            "сумма": replace(base, total=base.total + 0.01),
            "адрес": replace(base, addr=(base.addr or "") + "к2"),
            "название позиции": replace(
                base, items=(replace(first, name=first.name + "!"),) + base.items[1:]),
            "количество": replace(
                base, items=(replace(first, qty=first.qty + 0.001),) + base.items[1:]),
            "цена позиции": replace(
                base, items=(replace(first, price=first.price + 0.01),) + base.items[1:]),
        }
        for what, changed in variants.items():
            self.assertNotEqual(changed.digest, base.digest,
                                f"отпечаток не заметил, что изменилось: {what}")

    def test_digest_ignores_float_noise_below_a_kopeck_but_not_weight(self):
        """Рубли нормализуются до копеек, вес — до шестого знака.

        На нынешнем датасете круг «датасет → выгрузка → разбор» не расходится
        ни на одном из 1 744 чеков, то есть normalизация тут ничего не чинит —
        она страхует ШОВ: §8.10 обещает другие адаптеры, а сумма, собранная
        сложением позиций, приходит с накопленной погрешностью. Проверяется
        поэтому само свойство, а не его следы в данных.

        Округление до копеек и округление веса — разные требования, и путать их
        нельзя: 6,665 кг и 6,666 кг это разные покупки, а 1 234,56 ₽ и
        1 234,56 ₽ плюс 10⁻¹² — одна.
        """
        base = self.dataset[0]
        money_noise = replace(base, total=base.total + 1e-12)
        self.assertEqual(money_noise.digest, base.digest,
                         "отпечаток разошёлся от погрешности меньше копейки")

        first = base.items[0]
        weight = replace(base, items=(replace(first, qty=first.qty + 0.001),)
                         + base.items[1:])
        self.assertNotEqual(weight.digest, base.digest,
                            "отпечаток не различает вес до грамма")

    def test_fiscal_identity_wins_over_content(self):
        """Пока реквизиты есть, слабый ключ не используется вовсе."""
        sample = self.dataset[0]
        signed = self.parse_one(sample, fn="928744", fd=1, fpd=2)
        self.assertEqual(signed.identity, ("fiscal", "928744:1:2"))
        self.assertNotEqual(signed.identity[1], signed.digest)


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
