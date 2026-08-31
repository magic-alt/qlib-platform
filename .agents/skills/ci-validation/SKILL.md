---
name: ci-validation
description: Select and run qlib-platform’s proportionate targeted tests and pre-PR gates, including governance-only validation for docs, Skills, Codex configuration, and workflows.
---

# qlib-platform validation

Start by mapping changed files to governing invariants and existing tests. Run the smallest deterministic tests first, including negative/failure-injection coverage when fail-closed contracts, PIT timing, snapshots, checkpoints, release verification, or execution boundaries change.

For local development, use only the repository-local interpreter. Windows: `$RepoPython = '.\\.venv\\Scripts\\python.exe'`. macOS/Linux: `RepoPython=.venv/bin/python`. Do not substitute system Python, bare `tq`, bare `qrun`, or Makefile targets when the repository-local environment is required.

## Proportionate validation

Pure documentation, `.agents/`, or `.codex/` changes should validate governance structure, documentation links/invariants, contract references, and project audit without pretending that a full model/backtest matrix adds evidence to a text-only change. Changes to source, configs, contracts, tests, dependencies, deployment assets, or `.github/workflows/` require the normal full CI path.

For a normal code change, run the relevant targeted tests first, for example:

```powershell
& $RepoPython -m pytest tests/test_phase3_contract.py tests/test_phase3_diagnostics.py
```

Before a code/config/contract PR, run the local equivalents of the full quality gates:

```powershell
& $RepoPython -m ruff check src tests
& $RepoPython -m ruff format --check src tests
& $RepoPython -m mypy src
& $RepoPython scripts/check_docs.py --root .
& $RepoPython -m tushare_qlib --config configs/pipeline.integrated.yaml validate-qrun-contract
& $RepoPython -m pytest --cov=src/tushare_qlib --cov-report=term-missing --cov-fail-under=60
& $RepoPython -m tushare_qlib project-audit --root . --output <temporary-path>
```

For governance configuration changes, also parse every `.codex/*.toml` and `.codex/agents/*.toml`, verify required custom-agent fields, validate Skill front matter/name alignment, and parse workflow YAML. When Codex CLI is available, validate changed `.rules` behavior with `codex execpolicy check` and its inline `match`/`not_match` cases.

Do not use `backfill`, `stage-*`, `dump-*`, `daily-sync`, `release build-*`, `release promote`, `dataset-promote`, `model-refit`, `model-deploy`, `model-rollback`, `daily-signal-run`, outbox delivery, ops acknowledgements/retries, scheduled-task changes, or `phase3-diagnose` as validation without explicit user authorization. Report every command run, result, skipped gate, and reason.
