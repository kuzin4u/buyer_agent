"""Приём выгрузок ФНС: сырое хранится как пришло, корпус собирается из него.

**Сырое хранится как пришло, и это главное решение модуля.** Выгрузка кладётся
в `data/inbox/` байт в байт, без разбора и без нормализации. Корпус — чистая
функция от сырых выгрузок и разбора: поумнеет разбор, корпус пересоберётся
заново и станет лучше. Обратный порядок — хранить разобранное и выбросить
исходник — необратим: всё, чего разбор не понял в тот день, теряется навсегда,
а вернуть это можно только повторной выгрузкой у ФНС.

**Эталонный датасет и рабочий корпус разведены.** `data/receipts.json` —
эталон: он заморожен, по нему считается `docs/BASELINE.txt` и на нём стоят
тесты. Рабочий корпус — эталон плюс принятые выгрузки. Пока выгрузок нет, корпус
СОВПАДАЕТ с эталоном чек в чек, поэтому развод ничего не сдвигает сегодня и
перестаёт ронять тесты завтра, когда данные начнут приходить. Без этого первая
же загрузка сделала бы красным весь набор, и правило «остановиться перед сдвигом
контрольных цифр» (Р-6) превратилось бы из защиты в помеху.

**Порядок источников: эталон первым, выгрузки по времени приёма.** При склейке
побеждает первый, и это не произвол. У эталона названия продавцов сведены к
читаемым, у боевой выгрузки они сырые; оставляя эталонную запись, мы сохраняем
уже проделанную работу. Цена — потерянные реквизиты у перекрытых чеков, и она
честная: тождество нужно, чтобы не задвоить, а не чтобы украсить.

**Отброшенное считается по видам и печатается.** Дубль по реквизитам — факт,
дубль по совпадению содержимого — тоже факт, а дубль по слабому ключу «дата и
сумма» — обоснованная догадка. Смешать их в одно число «отброшено N» значит
выдать догадку за факт (Р-21).
"""

import datetime
import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass, field

from .adapters.receipts_fns import load_receipts, parse_any
from .config import BASE, dataset_path

#: Куда кладутся сырые выгрузки. Внутри — файлы как пришли, имя даёт порядок
#: приёма и отпечаток содержимого.
INBOX = os.path.join("data", "inbox")

#: Переменная окружения с путём к приёмнику. Читается при обращении, а не при
#: импорте, — по той же причине, что и путь к базе: тесты не должны писать в
#: рабочий приёмник, а на площадке данные живут вне кода (Р-5).
INBOX_ENV = "BUYER_AGENT_INBOX"

#: Вид тождества, по которому чек признан дублем.
FISCAL = "fiscal"        # ФН:ФД:ФПД совпали — факт
DIGEST = "digest"        # содержимое совпало целиком — факт
WEAK = "weak"            # совпали дата и сумма — обоснованная догадка


def inbox_dir(base=BASE):
    return os.environ.get(INBOX_ENV) or os.path.join(base, INBOX)


def exports(base=BASE):
    """Принятые выгрузки в порядке приёма. Имя файла задаёт порядок."""
    folder = inbox_dir(base)
    if not os.path.isdir(folder):
        return []
    return [os.path.join(folder, name) for name in sorted(os.listdir(folder))
            if name.endswith(".json")]


@dataclass(frozen=True)
class Saved:
    """Принятая выгрузка: где лежит и чем опознаётся."""

    path: str
    name: str
    sha256: str
    receipts: int
    span: tuple = None

    @property
    def short(self):
        return self.sha256[:12]


class Rejected(ValueError):
    """Выгрузка не принята. Причина — для человека, а не для журнала ошибок."""


def _span(receipts):
    stamps = sorted(r.dt for r in receipts if r.dt)
    return (stamps[0], stamps[-1]) if stamps else None


def save_export(blob, base=BASE, received=None, filename=None):
    """Сохранить выгрузку как пришла — после того, как убедились, что это она.

    Разбор идёт ДО записи, и это единственная причина, по которой сырое здесь
    не сохраняется безусловно: файл, из которого не вышло ни одного чека, не
    станет корпусом никогда, а лежать в приёмнике будет и ломать сборку. Байты
    при этом пишутся исходные, а не то, что получилось после разбора.
    """
    if not blob:
        raise Rejected("пустой файл")
    try:
        receipts = parse_any(json.loads(blob.decode("utf-8")))
    except Exception as error:                    # noqa: BLE001 — причина нужна целиком
        raise Rejected(f"не разобрать как выгрузку ФНС: {error}") from None
    if not receipts:
        raise Rejected("в файле нет ни одного чека: это не выгрузка ФНС "
                       "или выгрузка пустая")

    digest = hashlib.sha256(blob).hexdigest()
    folder = inbox_dir(base)
    os.makedirs(folder, exist_ok=True)
    for existing in exports(base):
        if os.path.basename(existing).split("-", 1)[-1][:12] == digest[:12]:
            raise Rejected("эта выгрузка уже принята: файл совпадает побайтово")

    received = received or datetime.datetime.now()
    name = f"{received:%Y%m%dT%H%M%S}-{digest[:12]}.json"
    path = os.path.join(folder, name)
    with open(path, "wb") as f:
        f.write(blob)
    return Saved(path=path, name=name, sha256=digest, receipts=len(receipts),
                 span=_span(receipts))


@dataclass(frozen=True)
class SourceReport:
    name: str
    kind: str            # 'seed' | 'export'
    seen: int
    added: int

    @property
    def dropped(self):
        return self.seen - self.added


@dataclass(frozen=True)
class Corpus:
    """Склеенный корпус и отчёт о том, чем за склейку заплатили."""

    receipts: tuple
    sources: tuple = ()
    dropped: dict = field(default_factory=dict)

    @property
    def seen(self):
        return sum(s.seen for s in self.sources)

    @property
    def kept(self):
        return len(self.receipts)

    @property
    def dropped_total(self):
        return sum(self.dropped.values())

    @property
    def by_requisites(self):
        return self.dropped.get(FISCAL, 0)

    @property
    def by_content(self):
        return self.dropped.get(DIGEST, 0)

    @property
    def by_guess(self):
        """Отброшено по слабому ключу — единственное, что здесь догадка."""
        return self.dropped.get(WEAK, 0)

    @property
    def span(self):
        return _span(self.receipts)

    @property
    def with_requisites(self):
        """Сколько чеков корпуса вообще опознаваемы точно."""
        return sum(1 for r in self.receipts if r.fiscal_id is not None)


def _weak_key(receipt):
    """Слабый ключ: дата с точностью до минуты и сумма чека.

    Нужен там, где реквизитов нет ни у одной из сторон или есть только у одной:
    эталон получен преобразованием и реквизитов не несёт, поэтому совпадение с
    боевой выгрузкой по содержимому не гарантировано — продавец у них записан
    по-разному. На эталоне пара (дата, сумма) различает все 1 744 чека.
    """
    return (receipt.dt, round(receipt.total or 0.0, 2))


def merge(sources):
    """Склеить источники, отбрасывая дубли. Первый источник побеждает.

    Три ключа применяются по убыванию силы, и каждый считается отдельно.
    Слабый ключ НЕ применяется, когда реквизиты есть у обеих сторон и не
    совпали: это доказанно разные чеки, и совпадение даты с суммой их не
    склеивает. Иначе две настоящие покупки в одну минуту на одну сумму стали бы
    одной — а такое бывает (в эталоне три чека делят отметку времени).
    """
    kept = []
    by_fiscal, by_digest, by_weak = {}, {}, {}
    dropped = Counter()
    reports = []

    for name, kind, receipts in sources:
        added = 0
        for receipt in receipts:
            fiscal = receipt.fiscal_id
            if fiscal is not None and fiscal in by_fiscal:
                dropped[FISCAL] += 1
                continue
            digest = receipt.digest
            twin = by_digest.get(digest)
            if twin is None:
                weak = _weak_key(receipt)
                candidate = by_weak.get(weak)
                # Слабый ключ не бьёт реквизиты: если они есть у обеих сторон и
                # не совпали, это доказанно разные чеки, и совпадение даты с
                # суммой их не склеивает.
                if candidate is not None and not (fiscal and candidate.fiscal_id):
                    twin = candidate
                    kind_of_drop = WEAK
                else:
                    twin = None
            else:
                kind_of_drop = DIGEST

            if twin is not None:
                dropped[kind_of_drop] += 1
                # Реквизиты отброшенного запоминаются за тем чеком, который
                # остался. Тождество — факт о чеке, а не о записи: эталон своих
                # реквизитов не несёт, и, узнав их из перекрывшейся выгрузки, мы
                # опознаем тот же чек в следующей уже точно, а не догадкой.
                if fiscal is not None:
                    by_fiscal.setdefault(fiscal, twin)
                continue

            if fiscal is not None:
                by_fiscal[fiscal] = receipt
            by_digest[digest] = receipt
            by_weak.setdefault(_weak_key(receipt), receipt)
            kept.append(receipt)
            added += 1
        reports.append(SourceReport(name=name, kind=kind, seen=len(receipts),
                                    added=added))

    return Corpus(receipts=tuple(kept), sources=tuple(reports),
                  dropped=dict(dropped))


def build(base=BASE, dataset=None):
    """Рабочий корпус: эталонный датасет плюс принятые выгрузки.

    Пока приёмник пуст, корпус равен эталону чек в чек и в том же порядке —
    именно поэтому развод эталона и корпуса ничего не сдвигает, пока данные не
    начали приходить.
    """
    path = dataset or dataset_path(base)
    sources = [("эталонный датасет", "seed", load_receipts(path))]
    for export in exports(base):
        sources.append((os.path.basename(export), "export",
                        load_receipts(export)))
    return merge(sources)
