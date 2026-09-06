from __future__ import annotations

import argparse
import json
import os
import re
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
        default=77.9,
        help="blocking repository-wide coverage floor; ratchet upward as coverage improves",
    )
    parser.add_argument(
        "--coverage-target",
        type=float,
        default=85.0,
        help="repository-wide coverage target reported for convergence tracking",
    )
    parser.add_argument(
        "--diff-coverage-threshold",
        type=float,
        default=85.0,
        help="minimum coverage for changed executable production lines when --diff-base is supplied",
    )
    parser.add_argument(
        "--diff-base",
        default=None,
        help="git base SHA/ref for changed-line coverage; omitted for local runs without a comparison base",
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


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _changed_lines(*, root: Path, diff_base: str) -> dict[str, set[int]]:
    completed = subprocess.run(
        [
            "git",
            "diff",
            "--unified=0",
            "--no-ext-diff",
            diff_base,
            "HEAD",
            "--",
            "src/qlib_platform",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"git diff failed for base {diff_base!r}: {detail}")

    changed: dict[str, set[int]] = {}
    current_path: str | None = None
    for line in completed.stdout.splitlines():
        if line.startswith("+++ b/"):
            current_path = line[6:]
            changed.setdefault(current_path, set())
            continue
        if line.startswith("+++ /dev/null"):
            current_path = None
            continue
        match = _HUNK_RE.match(line)
        if match is None or current_path is None:
            continue
        start = int(match.group(1))
        count = int(match.group(2) or "1")
        if count > 0:
            changed[current_path].update(range(start, start + count))
    return {path: lines for path, lines in changed.items() if lines}


def _diff_coverage(
    *,
    coverage_payload: dict[str, object],
    changed_lines: dict[str, set[int]],
) -> tuple[float | None, int, int]:
    files = coverage_payload.get("files")
    if not isinstance(files, dict):
        raise ValueError("coverage JSON is missing files mapping")

    executable_changed = 0
    covered_changed = 0
    for path, line_numbers in changed_lines.items():
        file_payload = files.get(path)
        if not isinstance(file_payload, dict):
            continue
        executed = {int(value) for value in file_payload.get("executed_lines", [])}
        missing = {int(value) for value in file_payload.get("missing_lines", [])}
        executable = line_numbers & (executed | missing)
        executable_changed += len(executable)
        covered_changed += len(executable & executed)

    if executable_changed == 0:
        return None, 0, 0
    return covered_changed / executable_changed * 100.0, covered_changed, executable_changed


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.root).expanduser().resolve()
    if not (root / "pyproject.toml").is_file():
        raise SystemExit(f"not a qlib-platform repository root: {root}")
    for label, value in (
        ("coverage threshold", args.coverage_threshold),
        ("coverage target", args.coverage_target),
        ("diff coverage threshold", args.diff_coverage_threshold),
    ):
        if not 0 < value <= 100:
            raise SystemExit(f"{label} must be in (0, 100]")
    if args.coverage_threshold > args.coverage_target:
        raise SystemExit("coverage threshold cannot exceed coverage target")

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
    coverage_payload: dict[str, object] | None = None
    if coverage_json.is_file():
        try:
            parsed = json.loads(coverage_json.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                coverage_payload = parsed
                coverage_percent = float(parsed["totals"]["percent_covered"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            coverage_percent = None

    diff_coverage_percent: float | None = None
    diff_covered_lines = 0
    diff_executable_lines = 0
    diff_gate_passed = True
    diff_error: str | None = None
    if args.diff_base and coverage_payload is not None:
        try:
            changed = _changed_lines(root=root, diff_base=args.diff_base)
            diff_coverage_percent, diff_covered_lines, diff_executable_lines = _diff_coverage(
                coverage_payload=coverage_payload,
                changed_lines=changed,
            )
            if diff_coverage_percent is not None:
                diff_gate_passed = diff_coverage_percent >= args.diff_coverage_threshold
        except (RuntimeError, ValueError) as exc:
            diff_gate_passed = False
            diff_error = str(exc)

    checks_passed = all(result.passed for result in results) and len(results) == len(checks)
    passed = checks_passed and diff_gate_passed
    payload = {
        "schemaVersion": 2,
        "passed": passed,
        "coverageThreshold": float(args.coverage_threshold),
        "coverageTarget": float(args.coverage_target),
        "coveragePercent": coverage_percent,
        "coverageGapToTarget": (
            max(0.0, float(args.coverage_target) - coverage_percent)
            if coverage_percent is not None
            else None
        ),
        "diffCoverage": {
            "base": args.diff_base,
            "threshold": float(args.diff_coverage_threshold),
            "percent": diff_coverage_percent,
            "coveredExecutableLines": diff_covered_lines,
            "executableLines": diff_executable_lines,
            "passed": diff_gate_passed,
            "error": diff_error,
        },
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
        print(
            f"Repository coverage: {coverage_percent:.2f}% "
            f"(floor {args.coverage_threshold:.2f}%, target {args.coverage_target:.2f}%)"
        )
    if args.diff_base:
        if diff_error:
            print(f"Changed-line coverage: FAIL ({diff_error})")
        elif diff_coverage_percent is None:
            print("Changed-line coverage: N/A (no changed executable production lines)")
        else:
            print(
                f"Changed-line coverage: {diff_coverage_percent:.2f}% "
                f"({diff_covered_lines}/{diff_executable_lines}, required {args.diff_coverage_threshold:.2f}%)"
            )
    print(f"JSON report: {output}")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
