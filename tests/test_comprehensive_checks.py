from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import run_comprehensive_checks as checks


def _completed(command: list[str], return_code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, return_code, stdout="ok\n", stderr="")


def test_comprehensive_runner_executes_all_checks_and_reads_coverage(tmp_path, monkeypatch) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    output = tmp_path / "result.json"
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if any(str(item).startswith("--cov-report=json:") for item in command):
            coverage_arg = next(str(item) for item in command if str(item).startswith("--cov-report=json:"))
            coverage_path = Path(coverage_arg.split(":", 1)[1])
            coverage_path.parent.mkdir(parents=True, exist_ok=True)
            coverage_path.write_text(json.dumps({"totals": {"percent_covered": 87.25}}), encoding="utf-8")
        return _completed(command)

    monkeypatch.setattr(checks.subprocess, "run", fake_run)
    result = checks.main(["--root", str(tmp_path), "--coverage-threshold", "85", "--output", str(output)])
    assert result == 0
    assert len(commands) == 7
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["coveragePercent"] == pytest.approx(87.25)
    assert payload["coverageThreshold"] == pytest.approx(85.0)
    assert [item["name"] for item in payload["checks"]] == [
        "ruff-lint",
        "ruff-format",
        "mypy",
        "docs-contract",
        "qrun-contract",
        "project-audit",
        "pytest-coverage",
    ]


def test_comprehensive_runner_reports_failures_but_continues_by_default(tmp_path, monkeypatch) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    output = tmp_path / "result.json"
    call_count = 0

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        nonlocal call_count
        call_count += 1
        return _completed(command, return_code=1 if call_count == 1 else 0)

    monkeypatch.setattr(checks.subprocess, "run", fake_run)
    result = checks.main(["--root", str(tmp_path), "--output", str(output), "--skip-governance"])
    assert result == 1
    assert call_count == 4
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["passed"] is False
    assert payload["coveragePercent"] is None


def test_comprehensive_runner_fail_fast_stops_after_first_failure(tmp_path, monkeypatch) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    output = tmp_path / "result.json"
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return _completed(command, return_code=2)

    monkeypatch.setattr(checks.subprocess, "run", fake_run)
    assert (
        checks.main(
            [
                "--root",
                str(tmp_path),
                "--output",
                str(output),
                "--skip-governance",
                "--fail-fast",
            ]
        )
        == 1
    )
    assert len(commands) == 1


def test_comprehensive_runner_rejects_invalid_root_and_threshold(tmp_path) -> None:
    with pytest.raises(SystemExit, match="not a qlib-platform"):
        checks.main(["--root", str(tmp_path)])
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="coverage threshold"):
        checks.main(["--root", str(tmp_path), "--coverage-threshold", "0"])


def test_tail_retains_only_requested_suffix() -> None:
    assert checks._tail("abcdef", 3) == "def"
