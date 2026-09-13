"""Приём выгрузок ФНС: сырое как пришло, корпус из него, дубли по видам.

Главное, что здесь проверяется, — не арифметика склейки, а два обещания, на
которых держится весь контур.

*Эталон и корпус разведены, но пока не разошлись.* `data/receipts.json`
заморожен под `docs/BASELINE.txt`, работа идёт по корпусу. Пока приёмник пуст,
это ОДИН И ТОТ ЖЕ набор чеков в том же порядке — иначе развод сдвинул бы
контрольные цифры в тот же день, когда его сделали.

*Отброшенное не смешивается.* Дубль по реквизитам и дубль по совпадению даты с
суммой — разные утверждения: первое сказала касса, второе предположили мы. Одно
число «отброшено N» выдало бы догадку за факт (Р-21).
"""

import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fixture  # noqa: E402

from agent import intake as I                                  # noqa: E402
from agent.adapters.receipts_fns import load_dataset, load_receipts  # noqa: E402
from agent.config import dataset_path                          # noqa: E402
from test_loader import as_fns                                 # noqa: E402


class CorpusTest(unittest.TestCase):
    """Склейка на настоящем эталоне, в отдельной песочнице."""

    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(dataset_path(BASE))

    def setUp(self):
        self.home = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.home, "data"))
        shutil.copy(dataset_path(BASE),
                    os.path.join(self.home, "data", "receipts.json"))

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def accept(self, bodies, when=None):
        blob = json.dumps(bodies, ensure_ascii=False).encode("utf-8")
        return I.save_export(blob, base=self.home,
                             received=when or datetime.datetime(2026, 9, 13))

    # --- развод эталона и корпуса ---

    def test_empty_inbox_gives_exactly_the_reference_dataset(self):
        """Пока выгрузок нет, корпус равен эталону чек в чек и по порядку.

        Это и есть смысл развода: он ничего не сдвигает сегодня и перестаёт
        ронять тесты завтра. Если этот тест упадёт, значит развод начал менять
        числа сам по себе, и `docs/BASELINE.txt` больше не про те же данные.
        """
        corpus = I.build(base=self.home)
        self.assertEqual(list(corpus.receipts), list(self.dataset))
        self.assertEqual(corpus.kept, len(self.dataset))
        self.assertEqual(corpus.dropped, {})
        self.assertEqual([s.kind for s in corpus.sources], ["seed"])

    def test_reference_dataset_carries_no_requisites(self):
        corpus = I.build(base=self.home)
        self.assertEqual(corpus.with_requisites, 0)

    # --- сырое хранится как пришло ---

    def test_raw_export_is_stored_byte_for_byte(self):
        """Файл — источник, и он переживёт любой будущий разбор."""
        bodies = [as_fns(self.dataset[0], fn="928", fd=1, fpd=2)]
        blob = json.dumps(bodies, ensure_ascii=False).encode("utf-8")
        saved = I.save_export(blob, base=self.home,
                              received=datetime.datetime(2026, 9, 13, 12, 0, 0))
        with open(saved.path, "rb") as f:
            self.assertEqual(f.read(), blob, "сохранено не то, что пришло")
        self.assertIn(saved.sha256[:12], saved.name)

    def test_the_same_file_twice_is_refused(self):
        bodies = [as_fns(self.dataset[0], fn="928", fd=1, fpd=2)]
        self.accept(bodies)
        with self.assertRaises(I.Rejected) as caught:
            self.accept(bodies, when=datetime.datetime(2026, 9, 14))
        self.assertIn("уже принята", str(caught.exception))

    def test_rubbish_is_refused_and_not_stored(self):
        """Файл, из которого не вышло ни одного чека, в приёмник не ложится:
        он не станет корпусом никогда, а сборку ломать будет."""
        for blob in (b"", b"not json at all", b"{}", b"[]"):
            with self.assertRaises(I.Rejected):
                I.save_export(blob, base=self.home)
        self.assertEqual(I.exports(base=self.home), [])

    # --- дубли: виды считаются отдельно ---

    def test_duplicate_by_requisites_is_counted_as_a_fact(self):
        """Одна и та же выгрузка под двумя именами — дубль по реквизитам."""
        bodies = [as_fns(r, fn="928", fd=i, fpd=i) for i, r in
                  enumerate(self.dataset[:4])]
        self.accept(bodies, when=datetime.datetime(2026, 9, 13))
        # второй файл: те же чеки с теми же реквизитами, но другим адресом —
        # содержимое разойдётся, реквизиты нет.
        second = json.loads(json.dumps(bodies))
        for body in second:
            body["ticket"]["document"]["receipt"]["retailPlaceAddress"] = "иначе"
        self.accept(second, when=datetime.datetime(2026, 9, 14))

        corpus = I.build(base=self.home)
        self.assertEqual(corpus.by_requisites, 4)
        self.assertEqual(corpus.by_guess, 0,
                         "дубль по реквизитам засчитан как догадка")

    def test_duplicate_by_content_is_counted_as_a_fact(self):
        """Выгрузка без реквизитов, совпавшая с эталоном целиком."""
        self.accept([as_fns(r) for r in self.dataset[:6]])
        corpus = I.build(base=self.home)
        self.assertEqual(corpus.by_content, 6)
        self.assertEqual(corpus.kept, len(self.dataset))

    def test_requisites_learned_from_an_overlap_are_used_next_time(self):
        """Узнав реквизиты перекрывшегося чека, второй раз опознаём его точно.

        У эталона реквизитов нет, и первая выгрузка перекрывает его догадкой.
        Но реквизиты в ней БЫЛИ, и запомнить их не стоит ничего: следующая
        выгрузка того же чека отбрасывается уже по реквизитам, а не по
        совпадению даты с суммой. Догадка нужна один раз, дальше работает факт.
        """
        bodies = []
        for i, receipt in enumerate(self.dataset[:4]):
            body = as_fns(receipt, fn="928", fd=i, fpd=i)
            body["ticket"]["document"]["receipt"]["user"] = "СЫРОЕ ИМЯ"
            bodies.append(body)
        self.accept(bodies, when=datetime.datetime(2026, 9, 13))
        first = I.build(base=self.home)
        self.assertEqual(first.by_guess, 4, "первая выгрузка легла догадкой")

        # Та же четвёрка, снова иначе записанная, но с теми же реквизитами.
        again = json.loads(json.dumps(bodies))
        for body in again:
            body["ticket"]["document"]["receipt"]["user"] = "ЕЩЁ ИНАЧЕ"
        self.accept(again, when=datetime.datetime(2026, 9, 14))

        corpus = I.build(base=self.home)
        self.assertEqual(corpus.by_requisites, 4,
                         "реквизиты не запомнились, и чек снова опознан догадкой")
        self.assertEqual(corpus.kept, len(self.dataset), "корпус раздулся")

    def test_renamed_seller_falls_back_to_the_weak_key_and_says_so(self):
        """Тот же чек с сырым именем продавца — совпадения содержимого нет.

        Это главный случай ради которого слабый ключ вообще существует: эталон
        получен преобразованием, продавцы в нём сведены к читаемым, и боевая
        выгрузка тех же чеков не совпадёт с ним побайтово никогда.
        """
        bodies = []
        for i, receipt in enumerate(self.dataset[:6]):
            body = as_fns(receipt, fn="928", fd=i, fpd=i)
            body["ticket"]["document"]["receipt"]["user"] = \
                f'ООО "{(receipt.shop or "X").upper()}" ИНН 7712345678'
            bodies.append(body)
        self.accept(bodies)

        corpus = I.build(base=self.home)
        self.assertEqual(corpus.by_guess, 6, "слабый ключ не сработал")
        self.assertEqual(corpus.by_content, 0)
        self.assertEqual(corpus.by_requisites, 0)
        self.assertEqual(corpus.kept, len(self.dataset), "корпус раздулся")

    def test_the_seed_record_wins_and_keeps_its_readable_seller(self):
        """При перекрытии остаётся эталонная запись: её продавец уже разобран."""
        receipt = self.dataset[0]
        body = as_fns(receipt, fn="928", fd=1, fpd=2)
        body["ticket"]["document"]["receipt"]["user"] = "ООО РОГА И КОПЫТА"
        self.accept([body])

        corpus = I.build(base=self.home)
        same = [r for r in corpus.receipts if r.dt == receipt.dt
                and round(r.total, 2) == round(receipt.total, 2)]
        self.assertEqual(len(same), 1)
        self.assertEqual(same[0].shop, receipt.shop)

    def test_two_real_receipts_one_minute_apart_both_survive(self):
        """Слабый ключ не склеивает то, что реквизиты уже развели.

        Две настоящие покупки в одну минуту на одну сумму бывают: в эталоне три
        чека делят отметку времени. Если бы догадка била реквизиты, одна из них
        пропала бы молча.
        """
        first = as_fns(self.dataset[0], fn="928", fd=1, fpd=1)
        second = as_fns(self.dataset[0], fn="928", fd=2, fpd=2)
        for body in (first, second):
            body["ticket"]["document"]["receipt"]["dateTime"] = "2026-12-01T10:00:00"
            body["ticket"]["document"]["receipt"]["totalSum"] = 500000
        second["ticket"]["document"]["receipt"]["items"][0]["name"] = "ДРУГОЕ"
        self.accept([first, second])

        corpus = I.build(base=self.home)
        added = [r for r in corpus.receipts if r.dt == "2026-12-01T10:00:00"]
        self.assertEqual(len(added), 2, "разные чеки склеились по догадке")
        self.assertEqual(corpus.dropped, {})

    # --- корпус растёт ---

    def test_new_receipts_extend_the_corpus_and_its_span(self):
        bodies = []
        for i in range(3):
            body = as_fns(self.dataset[0], fn="928", fd=100 + i, fpd=100 + i)
            body["ticket"]["document"]["receipt"]["dateTime"] = \
                f"2026-10-0{i + 1}T10:00:00"
            body["ticket"]["document"]["receipt"]["totalSum"] = 111100 + i
            bodies.append(body)
        self.accept(bodies)

        corpus = I.build(base=self.home)
        self.assertEqual(corpus.kept, len(self.dataset) + 3)
        self.assertEqual(corpus.span[1], "2026-10-03T10:00:00")
        self.assertEqual(corpus.with_requisites, 3)
        report = [s for s in corpus.sources if s.kind == "export"][0]
        self.assertEqual((report.seen, report.added), (3, 3))

    def test_sources_are_reported_seed_first(self):
        self.accept([as_fns(self.dataset[0], fn="928", fd=1, fpd=2)])
        corpus = I.build(base=self.home)
        self.assertEqual([s.kind for s in corpus.sources], ["seed", "export"])
        self.assertEqual(corpus.sources[0].seen, len(self.dataset))


if __name__ == "__main__":
    unittest.main()
