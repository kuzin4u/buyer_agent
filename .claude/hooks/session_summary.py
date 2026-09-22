#!/usr/bin/env python3
"""Сводка по журналу хуков .claude/hooks.log.

Запуск из корня репозитория:
  python3 .claude/hooks/session_summary.py            — последняя сессия
  python3 .claude/hooks/session_summary.py --today    — всё за сегодня
  python3 .claude/hooks/session_summary.py --all      — весь журнал
  python3 .claude/hooks/session_summary.py --since 14:30

Журнал фиксирует попытки действий перед выполнением. Заблокированное
не выполнялось; разрешённое могло завершиться ошибкой самого инструмента.
"""
import collections
import datetime
import json
import os
import pathlib
import sys

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
REASONS = {
    "spec_edit": "правка SPEC.md",
    "spec_edit_override": "правка SPEC.md с разрешением владельца",
    "main_page": "правка главной sourdough-shop.html",
    "main_page_shell": "запись в главную через shell",
    "api_url_in_html": "адрес API в странице",
}


def load(path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def main(argv):
    root = pathlib.Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))
    log = root / ".claude" / "hooks.log"
    if not log.exists():
        print("Журнал пуст: .claude/hooks.log не найден.")
        return 0
    rows = load(log)
    if not rows:
        print("Журнал пуст.")
        return 0

    today = datetime.date.today().isoformat()
    if "--all" in argv:
        scope, sel = "весь журнал", rows
    elif "--today" in argv:
        scope, sel = f"сегодня ({today})", [r for r in rows if r["ts"].startswith(today)]
    elif "--since" in argv:
        t = argv[argv.index("--since") + 1]
        scope, sel = f"сегодня с {t}", [r for r in rows if r["ts"].startswith(today) and r["ts"][11:16] >= t]
    else:
        last = rows[-1].get("session", "")
        scope = f"последняя сессия {last[:8] or '(без id)'}"
        sel = [r for r in rows if r.get("session", "") == last]
    if not sel:
        print(f"Нет записей: {scope}.")
        return 0

    tools = collections.Counter(r["tool"] for r in sel)
    edits = [r for r in sel if r["tool"] in EDIT_TOOLS and r["decision"] == "ALLOW"]
    files = collections.Counter(r["target"] for r in edits)
    reads = collections.Counter(r["target"] for r in sel if r["tool"] == "Read")
    blocks = [r for r in sel if r["decision"] == "BLOCK"]
    block_reasons = collections.Counter(r["reason"] for r in blocks)
    overrides = [r for r in sel if r["reason"].endswith("_override")]

    print(f"Сводка: {scope}")
    print(f"Период: {sel[0]['ts'][11:19]} — {sel[-1]['ts'][11:19]} · событий: {len(sel)}")
    print("\nДействия по инструментам:")
    for t, n in tools.most_common():
        print(f"  {t:<14} {n}")
    print(f"\nПравки (разрешённые): {len(edits)} в {len(files)} файлах")
    for f, n in files.most_common(10):
        print(f"  {n:>3} × {f}")
    if reads:
        print(f"\nЧтение: {sum(reads.values())} раз, {len(reads)} файлов")
    print(f"\nБлокировки: {len(blocks)}")
    for r, n in block_reasons.most_common():
        print(f"  {n:>3} × {REASONS.get(r, r)}")
    if blocks:
        print("  Последние:")
        for r in blocks[-5:]:
            print(f"    {r['ts'][11:19]} {r['tool']} {r['target']}")
    if overrides:
        print(f"\nПравки с разрешением владельца: {len(overrides)}")
    repeat = [r for r, n in block_reasons.items() if n >= 2]
    if repeat:
        print("\n⚠ Повторные блокировки одного правила — сигнал по методике:")
        for r in repeat:
            print(f"  {REASONS.get(r, r)}: Claude пытался {block_reasons[r]} раз(а). Проверить формулировку в CLAUDE.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
