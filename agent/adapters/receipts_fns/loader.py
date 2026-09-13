"""Слой ввода: два формата, одна внутренняя модель (SPEC §3.2).

Боевой источник — выгрузка «Проверка чеков» (ФНС), суммы в копейках.
Пакетный датасет `data/receipts.json` — тот же материал в рублях.
Оба сходятся в Receipt/Item, дальше конвейер не знает, откуда пришли данные.

**Тождество чека берётся здесь, потому что больше негде.** Выгрузки
перекрываются по построению: человек выгружает «всё», а не «новое», — и без
признака, по которому чек равен самому себе, вторая выгрузка удваивает период.
Фискальные реквизиты (ФН, ФД, ФПД) такой признак дают, они приходят в выгрузке,
и стоит их однажды не прочитать — восстановить их из уже разобранного корпуса
нельзя ничем, кроме повторной выгрузки у ФНС. Поэтому они читаются на границе,
даже пока никто их не спрашивает.

Тождества два, и они не равны по силе. Фискальное выдано кассой и точное.
Отпечаток содержимого — слабее: он считается всегда, но два разных чека
теоретически могут совпасть, а один и тот же чек из двух источников — разойтись,
если источник что-то нормализовал. Поэтому вид тождества хранится рядом с
ключом: тот, кто склеивает, обязан знать, чем именно он склеил (Р-9).
"""

import datetime
import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    name: str      # «грязное» название, как в чеке
    price: float   # ₽ за единицу; у весовых это уже ₽/кг
    qty: float     # количество; дробное у весовых
    total: float   # ₽ за позицию


#: Виды тождества, от сильного к слабому. Печатаются в отчёте о склейке:
#: «отброшено 12 по реквизитам» и «отброшено 12 по совпадению содержимого» —
#: разные утверждения, и второе пользователь имеет право перепроверить.
FISCAL = "fiscal"        # ФН:ФД:ФПД — выдано кассой, точное
DIGEST = "digest"        # отпечаток содержимого — совпало всё, до копейки


@dataclass(frozen=True)
class Receipt:
    dt: str        # ISO, без таймзоны
    shop: str      # продавец; бывает пустым
    total: float   # ₽
    addr: str      # адрес; бывает пустым
    items: tuple
    #: Фискальные реквизиты. None — их в источнике не было; подставлять сюда
    #: что-либо нельзя: сравнивать None не с чем по построению, и чек без
    #: реквизитов не станет равен другому такому же по недосмотру (Р-9).
    fn: str = None    # ФН, fiscalDriveNumber — номер фискального накопителя
    fd: str = None    # ФД, fiscalDocumentNumber — номер документа
    fpd: str = None   # ФПД, fiscalSign — фискальный признак

    @property
    def year(self):
        return self.dt[:4]

    @property
    def fiscal_id(self):
        """Тождество, выданное кассой, или None.

        Нужны все три реквизита: ФД уникален только внутри своего накопителя, а
        ФПД подтверждает, что документ не подменён. Неполный набор — это не
        «частичное тождество», а его отсутствие.
        """
        if not (self.fn and self.fd and self.fpd):
            return None
        return f"{self.fn}:{self.fd}:{self.fpd}"

    @property
    def digest(self):
        """Отпечаток содержимого: считается всегда, совпадает побайтово.

        Числа округляются до той точности, в которой они вообще осмысленны:
        рубли до копеек, количество до шести знаков (весовой товар). Иначе
        отпечаток начнёт зависеть от того, через какой формат число прошло, и
        один и тот же чек из двух источников даст два разных ключа.
        """
        parts = [self.dt or "", self.shop or "", self.addr or "",
                 f"{round(self.total or 0, 2):.2f}"]
        for item in self.items:
            parts += [item.name or "", f"{round(item.price or 0, 2):.2f}",
                      f"{round(item.qty or 0, 6):.6f}",
                      f"{round(item.total or 0, 2):.2f}"]
        blob = "\x1f".join(parts).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:32]

    @property
    def identity(self):
        """(вид, ключ) — чем этот чек отличим от других.

        Вид возвращается вместе с ключом намеренно: склейка обязана считать
        отдельно то, что отбросила по реквизитам, и то, что отбросила по
        совпадению содержимого.
        """
        fiscal = self.fiscal_id
        return (FISCAL, fiscal) if fiscal else (DIGEST, self.digest)


def load_dataset(path):
    """`data/receipts.json` — суммы уже в рублях."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [
        Receipt(
            dt=r["dt"],
            shop=r.get("shop", ""),
            total=float(r["total"]),
            addr=r.get("addr", ""),
            items=tuple(
                Item(name=i["n"], price=float(i["p"]), qty=float(i["q"]),
                     total=float(i["s"]))
                for i in r["items"]),
        )
        for r in raw
    ]


def _walk(node):
    """Выгрузка ФНС приходит по-разному завёрнутой: список чеков, объект с
    ticket/document, иногда всё сразу. Ищем словари с items."""
    if isinstance(node, list):
        for x in node:
            yield from _walk(x)
    elif isinstance(node, dict):
        for wrapper in ("ticket", "document", "receipt"):
            if wrapper in node:
                yield from _walk(node[wrapper])
                return
        if "items" in node:
            yield node


def _fiscal(value):
    """Реквизит к строке. В выгрузке ФН приходит строкой, а ФД и ФПД числами."""
    if value is None or value == "":
        return None
    return str(value).strip() or None


def _dt_to_iso(value):
    if isinstance(value, (int, float)):
        return datetime.datetime.fromtimestamp(value).isoformat(timespec="seconds")
    s = str(value)
    return s.replace(" ", "T")[:19]


def parse_fns_export(raw):
    """Сырая выгрузка ФНС: суммы в КОПЕЙКАХ, продавец и адрес могут быть пусты."""
    out = []
    for r in _walk(raw):
        items = tuple(
            Item(
                name=i.get("name", ""),
                price=float(i.get("price", 0)) / 100.0,
                qty=float(i.get("quantity", 0)),
                total=float(i.get("sum", 0)) / 100.0,
            )
            for i in r.get("items") or ()
        )
        out.append(Receipt(
            dt=_dt_to_iso(r.get("dateTime", "")),
            shop=(r.get("user") or "").strip(),
            total=float(r.get("totalSum", 0)) / 100.0,
            addr=(r.get("retailPlaceAddress") or "").strip(),
            items=items,
            fn=_fiscal(r.get("fiscalDriveNumber")),
            fd=_fiscal(r.get("fiscalDocumentNumber")),
            fpd=_fiscal(r.get("fiscalSign")),
        ))
    out.sort(key=lambda x: x.dt)
    return out


def load_receipts(path):
    """Определяет формат по содержимому: развёрнутый датасет или выгрузка ФНС."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "items" in raw[0] \
            and raw[0]["items"] and "n" in raw[0]["items"][0]:
        return load_dataset(path)
    return parse_fns_export(raw)
