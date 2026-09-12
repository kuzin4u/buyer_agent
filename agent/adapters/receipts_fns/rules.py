"""Компиляция конфигов в рабочие правила.

Отдельный модуль, потому что порядок правил — часть логики, а не оформление
(SPEC §5, §11), и его нельзя размазывать по вызовам. Здесь конфиг превращается
в regexp'ы ровно один раз на загрузку.
"""

import re

RE_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
RE_WORD = re.compile(r"[A-Za-zА-Яа-яЁё]+")
# «12Х20СМ» — габариты упаковки, а не фасовка
RE_DIMS = re.compile(r"\d+\s*[ХX]\s*\d+\s*(СМ)?")
# жирность; вырезается ДО правил фасовки, иначе «СМЕТАНА 25%300Г» даст 25 (SPEC §5)
RE_FAT = re.compile(r"(\d+[.,]?\d*)\s*%")
RE_BARE_LITRES = re.compile(r"\b([01],\d{1,2})\s*$")
RE_PIECES = re.compile(r"(\d+)\s*ШТ\b")
RE_BARE_NUMBER = re.compile(r"(?<![.,\d])(\d{3,4})(?!\s*(Г|КГ|МЛ|Л|ШТ|%))\s*$")

#: правдоподобный диапазон штучной фасовки; больше — слипшийся код категории
PIECES_RANGE = (1, 60)

#: (имя, regexp, единица, множитель) — порядок значим, первое совпадение выигрывает
PACK_RULES = (
    ("кг", re.compile(r"(\d+[.,]?\d*)\s*КГ\b"), "kg", 1.0),
    ("г", re.compile(r"(\d+[.,]?\d*)\s*Г(?![А-Я])"), "kg", 0.001),
    ("л", re.compile(r"(\d+[.,]?\d*)\s*Л(?![А-Я])"), "l", 1.0),
    ("мл", re.compile(r"(\d+[.,]?\d*)\s*МЛ\b"), "l", 0.001),
)

UNIT_LABEL = {"kg": "кг", "l": "л", "pcs": "шт"}

#: обратное соответствие: так единица записана в конфиге
UNIT_BY_LABEL = {v: k for k, v in UNIT_LABEL.items()} | {"мл": "l", "г": "kg"}


class Rules:
    """Скомпилированные конфиги. Пересоздаётся при пополнении словарей (§8.11)."""

    def __init__(self, config):
        self.config = config
        norm = config.normalization

        self.homoglyphs = str.maketrans(
            {k: v for k, v in norm["homoglyphs"].items() if not k.startswith("_")})

        # Дефекты чтения формата: служебный префикс, слипшиеся пробелы.
        # Правила в конфиге, не в коде, и применяются ВСЕ подряд по порядку.
        cleanup = norm["input_cleanup"]
        self.cleanup = tuple((r["name"], re.compile(r["match"]), r.get("replace", ""))
                             for r in cleanup["rules"])
        self.trim = cleanup.get("trim", True)

        case = norm["case"]
        self.upper = case.get("upper", True)
        # Ё→Е только для сопоставления; отображаемое название сохраняет Ё
        self.fold = str.maketrans(case.get("fold_for_matching", {}))
        self.excluded = re.compile(norm["excluded_items"]["match"])
        self.weighted = re.compile(norm["weighted_goods"]["match"])
        # Хвост единицы измерения у весового названия: «ВЕС», «1КГ», «,КГ».
        # Знание о товаре, поэтому список правил в конфиге, а не здесь.
        key_name = norm["weighted_goods"]["key_name"]
        self.bulk_key = tuple((r["name"], re.compile(r["match"]), r.get("replace", ""))
                              for r in key_name["rules"])
        self.bulk_key_trim = key_name.get("trim_chars", " ,")
        self.min_observations = norm["aggregation"]["min_observations"]

        # Множества групп, от которых зависит чтение голого числа, — в конфиге,
        # а не здесь: это знание о товаре (П-2 закрыт решением Р-16). Ключ —
        # id группы из categories.json, и он обязан там существовать; проверяет
        # это _check_group_ids, потому что опечатка иначе молчит.
        pack = norm["pack_extraction"]
        by_name = {r["name"]: r for r in pack["rules"]}
        self.drink_groups = frozenset(by_name["голый объём напитка"]["only_if_groups"])
        units = by_name["голое число в конце названия"]["unit_by_group"]
        self.bare_default = UNIT_BY_LABEL[units["_default"]]
        self.bare_unit = {gid: UNIT_BY_LABEL[label]
                          for label, ids in units.items()
                          if not label.startswith("_") for gid in ids}

        self.shops = tuple((r, re.compile(r["match"])) for r in config.shops["rules"])
        self.food_share_threshold = config.shops["unknown_resolution"]["food_share_threshold"]
        self.categories = tuple((g, re.compile(g["match"])) for g in config.categories["groups"])
        self.brands = tuple((b, re.compile(b["match"])) for b in config.brands["brands"])

        # Группа, на которую ссылается правило фасовки, обязана существовать:
        # иначе правило молча перестаёт срабатывать при переименовании группы —
        # ровно тот дефект, из-за которого множества и осели в коде (П-2).
        unknown = (self.drink_groups | set(self.bare_unit)) \
            - {g["id"] for g in config.categories["groups"]}
        if unknown:
            raise ValueError(
                "normalization.json → pack_extraction ссылается на группы, "
                "которых нет в categories.json: " + ", ".join(sorted(unknown)))

    @property
    def fingerprint(self):
        return self.config.fingerprint
