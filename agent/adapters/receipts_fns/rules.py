"""Компиляция конфигов в рабочие правила.

Отдельный модуль, потому что порядок правил — часть логики, а не оформление
(SPEC §5, §11), и его нельзя размазывать по вызовам. Здесь конфиг превращается
в regexp'ы ровно один раз на загрузку.
"""

import re

# Категории, у которых голое дробное число в конце названия — литраж
# («ПИВО КЕРСАРИ СВ 0,45»). Только напитки: иначе правило ловит артикулы.
DRINK_GROUPS = frozenset({"voda", "sok", "pivo", "vino", "krepkiy"})

# Категории, у которых голое целое число в конце — миллилитры, а не граммы
# («МАЦУН ФМ 3,2 500» — 500 мл).
LIQUID_GROUPS = frozenset({"moloko", "kefir", "slivki", "voda", "sok",
                           "pivo", "vino", "krepkiy", "maslo_rast"})

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
        self.min_observations = norm["aggregation"]["min_observations"]

        self.shops = tuple((r, re.compile(r["match"])) for r in config.shops["rules"])
        self.food_share_threshold = config.shops["unknown_resolution"]["food_share_threshold"]
        self.categories = tuple((g, re.compile(g["match"])) for g in config.categories["groups"])
        self.brands = tuple((b, re.compile(b["match"])) for b in config.brands["brands"])

    @property
    def fingerprint(self):
        return self.config.fingerprint
