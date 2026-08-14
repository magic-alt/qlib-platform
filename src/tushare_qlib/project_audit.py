from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class AuditFinding:
    id: str
    severity: str
    passed: bool
    evidence: str
    remediation: str


_FORBIDDEN_DIRS = {".venv", "venv", "mlruns"}
_CACHE_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache"}
_NON_SOURCE_ROOTS = {".git", "data"}
_SECRET_PATTERNS = [
    re.compile(
        r"""(?i)TUSHARE_TOKEN\s*[:=]\s*(?!None\b|null\b|changeme|replace_me|your_|fake_|dummy_|\$\{|<REDACTED>)"""
        r"""["']?[A-Za-z0-9_-]{16,}["']?"""
    ),
    re.compile(
        r"""(?i)(api[_-]?key|secret|password)\s*[:=]\s*["']"""
        r"""(?!changeme|replace_me|your_|fake_|dummy_|<REDACTED>|\$\{)[^"']{8,}["']"""
    ),
]


def _source_files(base: Path) -> list[Path]:
    """Return tracked and untracked-but-not-ignored source files.

    This deliberately never opens ignored ``.env*`` files, generated datasets,
    virtual environments, or experiment artifacts.
    """

    completed = subprocess.run(
        ["git", "-C", str(base), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise RuntimeError("project audit requires a readable Git worktree")
    values = completed.stdout.decode("utf-8", errors="strict").split("\0")
    result: list[Path] = []
    for value in values:
        if not value:
            continue
        path = base / value
        if path.name == ".env" or path.name.startswith(".env.") and path.name != ".env.example":
            continue
        if path.is_file():
            result.append(path)
    return result


def audit_project(root: str | Path) -> dict[str, object]:
    base = Path(root).expanduser().resolve()
    files = _source_files(base)
    relative_files = [path.relative_to(base) for path in files]

    findings: list[AuditFinding] = []
    forbidden = sorted(
        str(path) for path in relative_files if any(part in _FORBIDDEN_DIRS for part in path.parts)
    )
    findings.append(
        AuditFinding(
            "SEC-001",
            "P0",
            not forbidden,
            f"forbidden_directories={forbidden[:20]}",
            "Exclude virtual environments and experiment artifacts from release packages.",
        )
    )
    caches = sorted(str(path) for path in relative_files if any(part in _CACHE_DIRS for part in path.parts))
    findings.append(
        AuditFinding(
            "PKG-002",
            "P3",
            not caches,
            f"cache_directories={caches[:20]}",
            "Clean generated caches before release; the packaging script excludes them automatically.",
        )
    )
    secret_hits: list[str] = []
    for path in files:
        if path.suffix.lower() in {".parquet", ".pkl", ".zip", ".png", ".jpg"}:
            continue
        if path.name == ".env.example":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if any(pattern.search(text) for pattern in _SECRET_PATTERNS):
            secret_hits.append(str(path.relative_to(base)))
    findings.append(
        AuditFinding(
            "SEC-002",
            "P0",
            not secret_hits,
            f"candidate_secret_files={secret_hits}",
            "Rotate exposed credentials and inject secrets only at runtime.",
        )
    )
    required = ["pyproject.toml", ".gitignore", "README.md", "Makefile", ".github/workflows/ci.yml"]
    missing = [name for name in required if not (base / name).exists()]
    findings.append(
        AuditFinding(
            "ENG-001", "P1", not missing, f"missing={missing}", "Add reproducible packaging and CI files."
        )
    )
    tests = list((base / "tests").glob("test_*.py")) if (base / "tests").exists() else []
    findings.append(
        AuditFinding(
            "QA-001",
            "P1",
            len(tests) >= 6,
            f"test_files={len(tests)}",
            "Cover data, portfolio, execution, research gate and bridge contracts.",
        )
    )
    findings.append(
        AuditFinding(
            "OPS-001",
            "P1",
            (base / "docs" / "OPERATIONS_RUNBOOK.md").exists(),
            "operations_runbook=" + str((base / "docs" / "OPERATIONS_RUNBOOK.md").exists()),
            "Document daily workflow, recovery, model rollback and incident response.",
        )
    )
    weighted = {"P0": 25, "P1": 10, "P2": 4, "P3": 1}
    max_penalty = sum(weighted[f.severity] for f in findings)
    penalty = sum(weighted[f.severity] for f in findings if not f.passed)
    score = round(100 * (1 - penalty / max(1, max_penalty)), 1)
    return {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "root": str(base),
        "score": score,
        "passed": all(f.passed for f in findings if f.severity in {"P0", "P1"}),
        "findings": [asdict(f) for f in findings],
    }


def write_audit(report: dict[str, object], path: str | Path) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return target
