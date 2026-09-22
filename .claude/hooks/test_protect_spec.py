#!/usr/bin/env python3
"""Тесты protect_spec.py и журнала. Запуск из корня репозитория:
   python3 .claude/hooks/test_protect_spec.py
Работает во временной папке — реальный .claude/hooks.log не трогает."""
import json, os, pathlib, subprocess, sys, tempfile
HERE = pathlib.Path(__file__).resolve().parent
HOOK, SUMMARY = HERE / "protect_spec.py", HERE / "session_summary.py"
CASES = [
    ("Edit SPEC.md → блок", {"tool_name": "Edit", "tool_input": {"file_path": "/repo/SPEC.md"}}, {}, 2),
    ("Write DECISIONS.md → проходит", {"tool_name": "Write", "tool_input": {"file_path": "/repo/docs/DECISIONS.md"}}, {}, 0),
    ("cat SPEC.md → проходит", {"tool_name": "Bash", "tool_input": {"command": "cat SPEC.md"}}, {}, 0),
    ("echo >> SPEC.md → блок", {"tool_name": "Bash", "tool_input": {"command": "echo x >> SPEC.md"}}, {}, 2),
    ("sed -i SPEC.md → блок", {"tool_name": "Bash", "tool_input": {"command": "sed -i s/a/b/ SPEC.md"}}, {}, 2),
    ("grep SPEC.md → проходит", {"tool_name": "Bash", "tool_input": {"command": "grep median SPEC.md | head"}}, {}, 0),
    ("Edit SPEC.md с SPEC_EDIT_ALLOWED=1 → проходит", {"tool_name": "Edit", "tool_input": {"file_path": "/repo/SPEC.md"}}, {"SPEC_EDIT_ALLOWED": "1"}, 0),
    ("Edit SPEC.md.bak → проходит", {"tool_name": "Edit", "tool_input": {"file_path": "/repo/src/SPEC.md.bak"}}, {}, 0),
    ("Read SPEC.md → проходит", {"tool_name": "Read", "tool_input": {"file_path": "/repo/SPEC.md"}}, {}, 0),
]
def run():
    tmp = tempfile.mkdtemp(); (pathlib.Path(tmp) / ".claude").mkdir()
    fails = 0
    for name, data, env, want in CASES:
        data = dict(data, session_id="test-session-0001")
        e = dict(os.environ, CLAUDE_PROJECT_DIR=tmp, **env)
        r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(data), text=True, capture_output=True, env=e)
        ok = r.returncode == want; fails += not ok
        print(f"{'OK ' if ok else 'FAIL'} {name} (код {r.returncode}, ждали {want})")
    log = (pathlib.Path(tmp) / ".claude" / "hooks.log").read_text().splitlines()
    ok = len(log) == len(CASES); fails += not ok
    print(f"{'OK ' if ok else 'FAIL'} журнал: {len(log)} записей из {len(CASES)}")
    s = subprocess.run([sys.executable, str(SUMMARY)], text=True, capture_output=True, env=dict(os.environ, CLAUDE_PROJECT_DIR=tmp))
    ok = "Блокировки: 3" in s.stdout and "Правки с разрешением владельца: 1" in s.stdout; fails += not ok
    print(f"{'OK ' if ok else 'FAIL'} сводка: 3 блокировки и 1 правка с разрешением")
    print("\n--- пример сводки ---\n" + s.stdout)
    print("ИТОГ:", "все тесты пройдены" if not fails else f"провалено: {fails}")
    return 1 if fails else 0
if __name__ == "__main__":
    sys.exit(run())
