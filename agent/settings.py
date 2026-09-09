"""Настройки пользователя (SPEC §3.3).

Живут ЛОКАЛЬНО и в репозиторий не попадают: `settings.local.json` исключён
в .gitignore. Датасет владелец решил публиковать, настройки — нет.

Здесь же оценки магазинов: качества и обстановки в чеках нет и быть не может,
это ручной ввод пользователя (SPEC §8.7).
"""

import json
import os
from dataclasses import dataclass, field, asdict

from .config import BASE

FILENAME = "settings.local.json"


@dataclass
class Settings:
    #: группы, которые обязаны быть в постоянной корзине, даже если редкие
    required_groups: tuple = ()
    #: группы, которые пользователь не хочет видеть в корзине
    excluded_groups: tuple = ()
    #: основной магазин по умолчанию; None — вывести из истории
    main_venue: str = None
    #: целевой бюджет корзины, ₽
    budget: float = None
    #: включать ли ярус food_candidate (SPEC §7: по умолчанию выключен)
    include_candidates: bool = False
    #: {магазин: {"quality": 1..5, "ambience": 1..5}} — только ручной ввод
    venue_ratings: dict = field(default_factory=dict)

    @classmethod
    def load(cls, base=BASE):
        """Отсутствие файла — не ошибка: агент работает на умолчаниях."""
        path = os.path.join(base, FILENAME)
        if not os.path.exists(path):
            return cls()
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        known = {f.name for f in cls.__dataclass_fields__.values()}
        data = {k: v for k, v in raw.items() if k in known}
        for key in ("required_groups", "excluded_groups"):
            if key in data:
                data[key] = tuple(data[key])
        return cls(**data)

    def save(self, base=BASE):
        path = os.path.join(base, FILENAME)
        payload = asdict(self)
        payload["required_groups"] = list(self.required_groups)
        payload["excluded_groups"] = list(self.excluded_groups)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return path

    def rating(self, venue):
        """→ (качество, обстановка) или (None, None), если пользователь не оценил."""
        r = self.venue_ratings.get(venue) or {}
        return r.get("quality"), r.get("ambience")
