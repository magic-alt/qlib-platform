---
name: ci-validation
description: Select and run qlib-platform’s proportionate targeted tests and pre-PR CI gates after a change, using only the repository-local Python environment.
---

# qlib-platform validation

Start by mapping the changed files to governing invariants and existing tests. Run the smallest targeted deterministic tests first, including failure-injection tests when a fail-closed contract path, PIT timing, snapshot, checkpoint, or execution boundary changes.

On Windows, define `$RepoPython = '.\.venv\Scripts\python.exe'`; on macOS/Linux, define `RepoPython=.venv/bin/python`. Do not use a system Python, bare `tq`, bare `qrun`, or Makefile target. If the repository-local interpreter is missing, stop and restore the environment rather than substituting a global interpreter.

For a normal code change, run the relevant targeted test files first, for example:

```powershell
$RepoPython = '.\.venv\Scripts\python.exe'
& $RepoPython -m pytest tests/test_phase3_contract.py tests/test_phase3_diagnostics.py
```

Before a PR, run the local CI equivalents appropriate to the diff:

```powershell
$RepoPython = '.\.venv\Scripts\python.exe'
& $RepoPython -m ruff check src tests
& $RepoPython -m ruff format --check src tests
& $RepoPython -m mypy src
& $RepoPython -m tushare_qlib --config configs/pipeline.yaml validate-qrun-contract
& $RepoPython -m pytest --cov=src/tushare_qlib --cov-report=term-missing --cov-fail-under=60
& $RepoPython -m tushare_qlib project-audit --root . --output <temporary-path>
```

Do not run `backfill`, `stage-*`, `dump-*`, `daily-sync`, `production-*`, `model-deploy`, `model-rollback`, scheduled-task changes, or `phase3-diagnose` as validation without explicit user authorization. Report every command run, result, skipped gate, and reason.
