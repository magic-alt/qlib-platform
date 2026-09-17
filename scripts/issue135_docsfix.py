from pathlib import Path

path = Path("docs/operations/sre-slo-and-game-day.md")
text = path.read_text(encoding="utf-8")
front_matter = """---
status: ACTIVE
owner: operations
applies_to_commit: 2b63bd64a19319a8e4579ca320299775aac9019b
last_verified: 2026-09-17
---

"""
if text.startswith("---\n"):
    raise SystemExit("SRE runbook already has front matter")
path.write_text(front_matter + text, encoding="utf-8")
