from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class CheckResult:
    name: str
    command: list[str]
    return_code: int
    duration_seconds: float
    stdout_tail: str
    stderr_tail: str

    @property
    def passed(self) -> bool:
        return self.return_code == 0


def _tail(text: str, limit: int = 8000) -> str:
    return text[-limit:]


def _run(name: str, command: list[str], *, root: Path) -> CheckResult:
    print(f"\n=== {name} ===")
    print("$ " + " ".join(command))
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=root,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
    )
    duration = time.monotonic() - started
    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="" if completed.stderr.endswith("\n") else "\n")
    print(f"[{name}] {'PASS' if completed.returncode == 0 else 'FAIL'} ({duration:.2f}s)")
    return CheckResult(
        name=name,
        command=command,
        return_code=completed.returncode,
        duration_seconds=round(duration, 3),
        stdout_tail=_tail(completed.stdout),
        stderr_tail=_tail(completed.stderr),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the repository-wide qlib-platform certification suite.")
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument(
        "--coverage-threshold",
        type=float,
        default=85.0,
        help="minimum repository-wide statement coverage",
    )
    parser.add_argument(
        "--output",
        default="artifacts/validation/comprehensive_checks.json",
        help="JSON result path, relative to repository root unless absolute",
    )
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument(
        "--skip-governance",
        action="store_true",
        help="skip docs/qrun/project-audit checks; intended only for focused developer iteration",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.root).expanduser().resolve()
    if not (root / "pyproject.toml").is_file():
        raise SystemExit(f"not a qlib-platform repository root: {root}")
    if not 0 < args.coverage_threshold <= 100:
        raise SystemExit("coverage threshold must be in (0, 100]")

    python = sys.executable
    validation_dir = root / "artifacts" / "validation"
    validation_dir.mkdir(parents=True, exist_ok=True)
    coverage_json = validation_dir / "coverage.json"
    project_audit = validation_dir / "project_audit.json"
    checks: list[tuple[str, list[str]]] = [
        ("ruff-lint", [python, "-m", "ruff", "check", "src", "tests", "scripts"]),
        ("ruff-format", [python, "-m", "ruff", "format", "--check", "src", "tests", "scripts"]),
        ("mypy", [python, "-m", "mypy", "src"]),
    ]
    if not args.skip_governance:
        checks.extend(
            [
                ("docs-contract", [python, "scripts/check_docs.py", "--root", "."]),
                (
                    "qrun-contract",
                    [
                        python,
                        "-m",
                        "qlib_platform",
                        "--config",
                        "configs/pipeline.integrated.yaml",
                        "validate-qrun-contract",
                    ],
                ),
                (
                    "project-audit",
                    [
                        python,
                        "-m",
                        "qlib_platform",
                        "project-audit",
                        "--root",
                        ".",
                        "--output",
                        str(project_audit),
                    ],
                ),
            ]
        )
    checks.append(
        (
            "pytest-coverage",
            [
                python,
                "-m",
                "pytest",
                "--cov=src/qlib_platform",
                "--cov-report=term-missing",
                f"--cov-report=json:{coverage_json}",
                f"--cov-fail-under={args.coverage_threshold:g}",
            ],
        )
    )

    results: list[CheckResult] = []
    started = time.monotonic()
    for name, command in checks:
        result = _run(name, command, root=root)
        results.append(result)
        if args.fail_fast and not result.passed:
            break

    coverage_percent: float | None = None
    if coverage_json.is_file():
        try:
            coverage_payload = json.loads(coverage_json.read_text(encoding="utf-8"))
            coverage_percent = float(coverage_payload["totals"]["percent_covered"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            coverage_percent = None

    payload = {
        "schemaVersion": 1,
        "passed": all(result.passed for result in results) and len(results) == len(checks),
        "coverageThreshold": float(args.coverage_threshold),
        "coveragePercent": coverage_percent,
        "durationSeconds": round(time.monotonic() - started, 3),
        "checks": [{**asdict(result), "passed": result.passed} for result in results],
    }
    output = Path(args.output)
    if not output.is_absolute():
        output = root / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nComprehensive result: {'PASS' if payload['passed'] else 'FAIL'}")
    if coverage_percent is not None:
        print(f"Repository coverage: {coverage_percent:.2f}% (required {args.coverage_threshold:.2f}%)")
    print(f"JSON report: {output}")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
