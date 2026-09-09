"""Загрузка конфигов и датасета.

Конфиги — файлы, не код (SPEC §6). Читаются в объект, а не в модульные глобалы:
пользователь пополняет словари прямо из интерфейса (§8.11), значит конфиги должны
перезагружаться в работающем процессе, а покрытие — пересчитываться.
"""

import hashlib
import json
import os
from dataclasses import dataclass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_FILES = {
    "shops": "config/shops.json",
    "normalization": "config/normalization.json",
    "categories": "config/categories.json",
    "brands": "config/brands.json",
}


@dataclass(frozen=True)
class Config:
    """Четыре конфига плюс отпечаток их содержимого."""

    shops: dict
    normalization: dict
    categories: dict
    brands: dict
    fingerprint: str
    base: str

    @classmethod
    def load(cls, base=BASE):
        raw = {}
        h = hashlib.sha256()
        for key, rel in CONFIG_FILES.items():
            path = os.path.join(base, rel)
            with open(path, "rb") as f:
                blob = f.read()
            h.update(rel.encode("utf-8"))
            h.update(blob)
            raw[key] = json.loads(blob.decode("utf-8"))
        return cls(fingerprint=h.hexdigest()[:16], base=base, **raw)


def dataset_path(base=BASE):
    return os.path.join(base, "data", "receipts.json")
