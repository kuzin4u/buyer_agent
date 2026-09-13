"""Загрузка конфигов и датасета.

Конфиги — файлы, не код (SPEC §6). Читаются в объект, а не в модульные глобалы:
пользователь пополняет словари прямо из интерфейса (§8.11), значит конфиги должны
перезагружаться в работающем процессе, а покрытие — пересчитываться.
"""

import copy
import hashlib
import json
import os
import re
from dataclasses import dataclass, replace

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


    # --- пополнения словарей пользователем (SPEC §8.11, Р-4) ---

    def with_rules(self, brands=(), categories=()):
        """Конфиг с пользовательскими правилами, проверяемыми ПЕРВЫМИ.

        Порядок не произвол, а смысл: пользователь смотрит на конкретную позицию
        и говорит, чем она является. Это утверждение сильнее общего правила из
        файла, и проверяться оно должно раньше. Иначе «ШОК РИТ МОЛ МИНД100»
        нельзя отнести к сладкому, пока в `moloko` стоит правило на «МОЛ» (П-6):
        общее правило совпадёт первым, и пополнение молча не подействует.

        Базовые конфиги не меняются — они курируемая основа и правятся вручную,
        с прогоном. Пользовательские правила живут в SQLite (Р-4) и ложатся
        сверху при загрузке.
        """
        if not brands and not categories:
            return self

        known = {g["id"]: g for g in self.categories["groups"]}
        groups = []
        for rule in categories:
            base = known.get(rule["group"])
            if base is None:
                raise ValueError(
                    f"правило ссылается на группу {rule['group']!r}, которой нет "
                    f"в categories.json")
            entry = copy.deepcopy(base)
            # Отдел и единица берутся у целевой группы: пользователь сказал «это
            # молоко», а не «это новая группа с другим отделом».
            entry["match"] = _compiled(rule["match"])
            entry["user"] = True
            entry["_comment"] = rule.get("note") or "добавлено пользователем"
            groups.append(entry)

        brand_rules = []
        for rule in brands:
            brand_rules.append({
                "brand": rule["brand"],
                "match": _compiled(rule["match"]),
                # Правило, введённое человеком про свою же покупку, —
                # подтверждённое по определению: подтверждать его некому больше.
                "confidence": "confirmed",
                "user": True,
                "note": rule.get("note") or "добавлено пользователем",
            })

        payload = json.dumps({"brands": brands, "categories": categories},
                             ensure_ascii=False, sort_keys=True)
        h = hashlib.sha256((self.fingerprint + payload).encode("utf-8"))
        return replace(
            self,
            categories={**self.categories,
                        "groups": groups + list(self.categories["groups"])},
            brands={**self.brands,
                    "brands": brand_rules + list(self.brands["brands"])},
            fingerprint=h.hexdigest()[:16])


def _compiled(pattern):
    """Проверить, что правило вообще является правилом.

    Пользователь вводит регулярку, и неверная должна отвергаться при добавлении
    — с понятным текстом, — а не ломать прогон конвейера через час.
    """
    try:
        re.compile(pattern)
    except re.error as error:
        raise ValueError(f"{pattern!r}: не регулярное выражение — {error}") from None
    return pattern


def dataset_path(base=BASE):
    return os.path.join(base, "data", "receipts.json")
