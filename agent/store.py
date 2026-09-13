"""Изменяемое состояние пользователя в SQLite (SPEC §3.3, DECISIONS Р-4).

Р-4 уже решил: SQLite держит изменяемое состояние, а чеки в неё не кладутся.
Здесь это решение исполняется для настроек §3.3 — обязательные и исключённые
продукты, оценки магазинов, бюджет, флаг кандидатов.

**Почему не JSON-файл, которым настройки были до сих пор.** Пока настройки
менялись из командной строки, файл годился. Веб их меняет по одной галочке, из
нескольких вкладок сразу, и запись целого файла на каждое нажатие теряет
соседнее изменение молча. Транзакция SQLite не теряет.

**Почему ключ-значение, а не колонка на настройку.** Состав настроек будет
расти: С6 добавит сюда пополнения словарей и подтверждения правил `probable`,
расширение Б — состояние алертов (Р-4). Колонка на настройку означала бы
миграцию схемы на каждую новую галочку. Здесь добавление настройки — это новое
поле датакласса `Settings` и ничего больше.

Формат значения — JSON, а не строка: `venue_ratings` вложенный, а
`required_groups` список, и хранить их текстом с разделителем значит заводить
второй разбор рядом с имеющимся.
"""

import json
import os
import sqlite3
from dataclasses import fields

from .config import BASE
from .settings import FILENAME as JSON_FILENAME, Settings

#: Файл состояния. В .gitignore: состояние у каждого пользователя своё (§3.3).
FILENAME = "state.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL      -- JSON: настройки бывают списками и словарями
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- Пополнения словарей пользователем (SPEC §8.11, Р-4). Базовые config/*.json
-- остаются курируемой основой и правятся вручную с прогоном; здесь то, что
-- человек добавил про свои покупки, и оно ложится поверх при загрузке.
CREATE TABLE IF NOT EXISTS rules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,          -- 'brand' | 'category'
    payload    TEXT NOT NULL,          -- JSON правила
    note       TEXT,
    effect     TEXT,                   -- JSON: что правило дало, измеренно
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS rules_unique ON rules (kind, payload);
-- Что уже отправлено пользователю. Состояние алертов живёт здесь, а не в боте:
-- бот не хранит ничего между сообщениями (ОА-1), иначе через два спринта
-- появится вторая копия правды, которую никто не пересчитывает.
CREATE TABLE IF NOT EXISTS sent (
    key     TEXT PRIMARY KEY,       -- что именно отправлено
    sent_at TEXT NOT NULL
);
"""


def path(base=BASE):
    return os.path.join(base, FILENAME)


class SettingsStore:
    """Настройки §3.3. Читает и пишет `Settings`, а не голые строки.

    Наружу отдаётся тот же датакласс `Settings`, с которым уже работает всё
    ядро. Это не прослойка ради прослойки: ядро не должно знать, откуда взялись
    настройки — из файла, из базы или из аргументов теста, — иначе хранилище
    придётся протаскивать в каждую функцию профиля.
    """

    def __init__(self, db_path=None, base=BASE):
        self.path = db_path or path(base)
        self.base = base
        self._conn = None

    # --- соединение ---

    @property
    def conn(self):
        if self._conn is None:
            directory = os.path.dirname(os.path.abspath(self.path))
            if directory:
                os.makedirs(directory, exist_ok=True)
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        return self._conn

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- чтение и запись ---

    #: Поля, которые в базе хранятся как кортежи, а приходят списками JSON.
    TUPLE_FIELDS = ("required_groups", "excluded_groups")

    def load(self):
        """→ Settings. Пустая база даёт умолчания, а не исключение."""
        known = {f.name for f in fields(Settings)}
        rows = self.conn.execute("SELECT key, value FROM settings").fetchall()
        data = {}
        for row in rows:
            if row["key"] not in known:
                continue          # настройка из будущей версии — не ломаемся
            value = json.loads(row["value"])
            if row["key"] in self.TUPLE_FIELDS and value is not None:
                value = tuple(value)
            data[row["key"]] = value
        return Settings(**data)

    def save(self, settings):
        """Записать все настройки одной транзакцией.

        Одной: настройки связаны между собой. Обязательная группа, попавшая в
        базу без исключённых, на мгновение даёт профиль, которого пользователь
        не просил, и если между этими двумя записями пересчитается корзина, она
        будет посчитана по несуществующему состоянию.
        """
        payload = []
        for field in fields(Settings):
            value = getattr(settings, field.name)
            if isinstance(value, tuple):
                value = list(value)
            payload.append((field.name, json.dumps(value, ensure_ascii=False)))
        with self.conn:          # транзакция: либо все настройки, либо никакие
            self.conn.executemany(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", payload)
        return settings

    def update(self, **changes):
        """Поменять часть настроек, не затирая остальные."""
        current = self.load()
        unknown = set(changes) - {f.name for f in fields(Settings)}
        if unknown:
            raise ValueError(f"неизвестные настройки: {sorted(unknown)}")
        for key, value in changes.items():
            if key in self.TUPLE_FIELDS and value is not None:
                value = tuple(value)
            setattr(current, key, value)
        return self.save(current)

    def rate(self, venue, quality=None, ambience=None):
        """Оценки магазина — ручной ввод пользователя (SPEC §8.7).

        Звёзды проверяются здесь, а не в веб-форме: в §8.7 сказано «звёзды
        1–5», и это свойство данных, а не поля ввода. Через год рядом с формой
        появится бот, и проверка должна быть одна.
        """
        current = self.load()
        ratings = dict(current.venue_ratings or {})
        entry = dict(ratings.get(venue) or {})
        for name, value in (("quality", quality), ("ambience", ambience)):
            if value is None:
                continue
            if value == "":          # пустое поле формы — снять оценку
                entry.pop(name, None)
                continue
            value = int(value)
            if not 1 <= value <= 5:
                raise ValueError(f"{name}={value}: звёзды бывают от 1 до 5 (§8.7)")
            entry[name] = value
        if entry:
            ratings[venue] = entry
        else:
            ratings.pop(venue, None)
        return self.update(venue_ratings=ratings)

    # --- миграция с JSON ---

    def migrated(self):
        return bool(self.conn.execute(
            "SELECT 1 FROM meta WHERE key = 'json_import'").fetchone())

    def import_json_once(self):
        """Перенести settings.local.json в базу, если он был, и запомнить это.

        Однократно и с отметкой: иначе база, из которой пользователь удалил
        обязательную группу, при следующем запуске получала бы её обратно из
        файла, который он уже не считает источником правды.
        """
        if self.migrated():
            return None
        json_path = os.path.join(self.base, JSON_FILENAME)
        imported = None
        if os.path.exists(json_path):
            imported = Settings.load(self.base)
            self.save(imported)
        with self.conn:
            self.conn.execute(
                "INSERT INTO meta (key, value) VALUES ('json_import', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (json.dumps({"file": JSON_FILENAME,
                             "found": imported is not None}, ensure_ascii=False),))
        return imported


class RulesStore(SettingsStore):
    """Пополнения словарей. Живут рядом с настройками: одно состояние, одна база.

    Правило пользователя — не черновик основного конфига, а отдельный слой.
    Смешивать их в один файл нельзя: основа курируется и проверяется прогоном
    против эталона, а пополнения добавляются по одному, из браузера, про
    конкретную позицию в чеке.
    """

    KINDS = ("brand", "category")

    def add(self, kind, payload, note=None):
        """Добавить правило. Повторное — не ошибка, а ничего.

        Проверка регулярки и существования группы делается конфигом
        (`Config.with_rules`), здесь — только хранение: правило должно
        отвергаться в одном месте, иначе бот и веб начнут расходиться.
        """
        if kind not in self.KINDS:
            raise ValueError(f"{kind!r}: бывают только {', '.join(self.KINDS)}")
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self.conn:
            cursor = self.conn.execute(
                "INSERT OR IGNORE INTO rules (kind, payload, note, created_at) "
                "VALUES (?, ?, ?, datetime('now'))", (kind, blob, note))
        # rowcount, а не lastrowid: у пропущенной вставки lastrowid остаётся от
        # предыдущей удачной, и вызывающий решил бы, что создал правило.
        return cursor.lastrowid if cursor.rowcount else None

    def remove(self, rule_id):
        with self.conn:
            self.conn.execute("DELETE FROM rules WHERE id = ?", (int(rule_id),))

    def rules(self, kind=None):
        """→ список правил, новые последними: порядок добавления — порядок смысла."""
        sql = "SELECT * FROM rules"
        args = ()
        if kind:
            sql += " WHERE kind = ?"
            args = (kind,)
        out = []
        for row in self.conn.execute(sql + " ORDER BY id", args):
            out.append({"id": row["id"], "kind": row["kind"],
                        "note": row["note"], "created_at": row["created_at"],
                        "effect": json.loads(row["effect"]) if row["effect"] else None,
                        **json.loads(row["payload"])})
        return out

    def overlay(self):
        """→ (правила брендов, правила категорий) для `Config.with_rules`."""
        def clean(rule):
            return {k: v for k, v in rule.items()
                    if k not in ("id", "kind", "created_at", "effect")}
        return ([clean(r) for r in self.rules("brand")],
                [clean(r) for r in self.rules("category")])

    def record_effect(self, rule_id, effect):
        """Запомнить, что правило дало. Без этого «полезно ли оно» забывается."""
        if rule_id is None:
            return
        with self.conn:
            self.conn.execute(
                "UPDATE rules SET effect = ? WHERE id = ?",
                (json.dumps(effect, ensure_ascii=False), int(rule_id)))


class Notifications(RulesStore):
    """Состояние уведомлений (ОА-1, Р-4).

    Бот — транспорт: он спрашивает ядро, что отправить, и отправляет. Решение,
    что пора напомнить, и память о том, что уже напомнили, — здесь. Без этой
    памяти напоминание уходило бы на каждый опрос, то есть каждые несколько
    минут.
    """

    #: Одно напоминание про одну группу не чаще, чем раз в столько дней.
    COOLDOWN_DAYS = 7

    def already_sent(self, key, within_days=None):
        within = self.COOLDOWN_DAYS if within_days is None else within_days
        row = self.conn.execute(
            "SELECT 1 FROM sent WHERE key = ? "
            "AND sent_at > datetime('now', ?)",
            (key, f"-{int(within)} days")).fetchone()
        return bool(row)

    def mark_sent(self, key):
        with self.conn:
            self.conn.execute(
                "INSERT INTO sent (key, sent_at) VALUES (?, datetime('now')) "
                "ON CONFLICT(key) DO UPDATE SET sent_at = excluded.sent_at",
                (key,))

    def due(self, items, within_days=None):
        """Отфильтровать то, о чём ещё не напоминали, и отметить отправленным.

        Отметка ставится здесь же, а не после успешной отправки: иначе сбой
        сети у транспорта превращается в повтор, а повтор раздражает сильнее,
        чем пропуск. Напоминание про просроченную группу придёт снова, когда
        истечёт пауза.
        """
        out = []
        for key, payload in items:
            if self.already_sent(key, within_days):
                continue
            self.mark_sent(key)
            out.append(payload)
        return out


def load_settings(base=BASE, db_path=None):
    """Настройки пользователя из базы, с однократным переносом старого файла."""
    with SettingsStore(db_path=db_path, base=base) as store:
        store.import_json_once()
        return store.load()
