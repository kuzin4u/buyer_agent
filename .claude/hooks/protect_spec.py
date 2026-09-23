#!/usr/bin/env python3
"""PreToolUse-хук: запрещает Claude менять SPEC.md и ведёт журнал действий.

Решение проекта: новые решения пишутся в docs/DECISIONS.md, SPEC.md задним
числом не правится. Код выхода 2 = блокировать; stderr получает Claude.
Осознанная правка SPEC владельцем: запустить claude с SPEC_EDIT_ALLOWED=1.
Журнал: .claude/hooks.log (JSON по строке), сводка — session_summary.py.
"""
import json
import os
import re
import sys

HOOK = "protect_spec"
PROTECTED = "SPEC.md"
MSG = ("SPEC.md защищён: решения проекта записываются в docs/DECISIONS.md, "
       "SPEC задним числом не правится. Добавь запись в docs/DECISIONS.md "
       "или попроси пользователя изменить SPEC.md самому.")
WRITE_OPS = re.compile(r"(>>?|\btee\b|\bsed\b[^|;&]*\s-i|\bmv\b|\bcp\b|\brm\b|\btruncate\b|\bdd\b|\bperl\b[^|;&]*\s-i|open\([^)]*['\"]w)")

def log_event(hook, data, decision, reason=""):
    """Пишет одну строку JSON в .claude/hooks.log. Сбой журнала не мешает работе."""
    try:
        import datetime, pathlib
        inp = data.get("tool_input", {}) or {}
        target = inp.get("file_path") or inp.get("notebook_path") or (inp.get("command") or "")[:120]
        rec = {
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "session": data.get("session_id", ""),
            "hook": hook,
            "tool": data.get("tool_name", ""),
            "target": target,
            "decision": decision,
            "reason": reason,
        }
        root = pathlib.Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))
        with (root / ".claude" / "hooks.log").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    tool = data.get("tool_name", "")
    inp = data.get("tool_input", {}) or {}
    allowed = os.environ.get("SPEC_EDIT_ALLOWED") == "1"

    blocked = False
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = inp.get("file_path") or inp.get("notebook_path") or ""
        blocked = os.path.basename(path) == PROTECTED
    elif tool == "Bash":
        cmd = inp.get("command", "")
        blocked = PROTECTED in cmd and bool(WRITE_OPS.search(cmd))

    if blocked and not allowed:
        log_event(HOOK, data, "BLOCK", "spec_edit")
        print(MSG, file=sys.stderr)
        return 2
    log_event(HOOK, data, "ALLOW", "spec_edit_override" if blocked else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
