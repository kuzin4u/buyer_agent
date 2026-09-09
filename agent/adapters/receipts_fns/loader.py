"""Слой ввода: два формата, одна внутренняя модель (SPEC §3.2).

Боевой источник — выгрузка «Проверка чеков» (ФНС), суммы в копейках.
Пакетный датасет `data/receipts.json` — тот же материал в рублях.
Оба сходятся в Receipt/Item, дальше конвейер не знает, откуда пришли данные.
"""

import datetime
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    name: str      # «грязное» название, как в чеке
    price: float   # ₽ за единицу; у весовых это уже ₽/кг
    qty: float     # количество; дробное у весовых
    total: float   # ₽ за позицию


@dataclass(frozen=True)
class Receipt:
    dt: str        # ISO, без таймзоны
    shop: str      # продавец; бывает пустым
    total: float   # ₽
    addr: str      # адрес; бывает пустым
    items: tuple

    @property
    def year(self):
        return self.dt[:4]


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
