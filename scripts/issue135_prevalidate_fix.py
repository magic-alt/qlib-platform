from pathlib import Path

path = Path("src/qlib_platform/runtime/sre.py")
text = path.read_text(encoding="utf-8")
old = '''            gate = str(event.get("failed_gate") or "")
            check = current_checks.get(gate)
            if check is None or check.get("status") != "PASS":
                continue
'''
new = '''            gate = str(event.get("failed_gate") or "")
            current_check = current_checks.get(gate)
            if current_check is None or current_check.get("status") != "PASS":
                continue
'''
if old not in text:
    raise SystemExit("alert resolution mypy anchor not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
