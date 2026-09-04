# Repository Guidelines

## Project Structure & Module Organization
- `src/qlib_platform/`: canonical Python package for the research platform. New implementation code belongs here.
- `src/qlib_platform/data/`: provider-neutral ingestion and data contracts; concrete vendor/transport adapters belong under `data/sources/`.
- `src/qlib_platform/`: deprecated import-compatibility namespace only. Do not add implementation modules here.
- `tests/`: pytest test suites (`test_*.py`).
- `scripts/`: utility entry scripts.
- `configs/`: pipeline and workflow YAML files.
- `docs/`: operational runbooks and API documentation.
- `.github/workflows/`: CI definitions; `constraints/`: CI dependency constraints.
- `data/`: generated/raw/curated output datasets (large artifacts, keep untracked).
- `mlruns/`: experiment metadata/artifacts from training runs.
- `README.md`: operational command reference.

## Build, Test, and Development Commands
- Run commands from the repository root and use only the repository-local interpreter: Windows PowerShell `.\.venv\Scripts\python.exe`; macOS/Linux `.venv/bin/python`. Do not use system `python`, `py`, globally installed Python, or bare `tq`/`qrun` commands. If this interpreter is absent, stop and recreate the local environment before proceeding.
- In PowerShell, define `$RepoPython = '.\.venv\Scripts\python.exe'`; in macOS/Linux shells, define `RepoPython=.venv/bin/python`. Invoke pipeline commands as `<repo-python> -m qlib_platform --config configs/pipeline.standalone.yaml <command>` unless a governed workflow names another profile. `python -m qlib_platform` remains a compatibility entry point only.
- Install core development dependencies: `<repo-python> -m pip install -c constraints/ci.txt -e ".[dev]"`.
- Install operational data dependencies when needed: `<repo-python> -m pip install -e ".[data]"`; the `data` extra currently contains the supported Tushare Pro and MySQL adapter dependencies. Install all operational dependencies with `<repo-python> -m pip install -e ".[all,dev]"`; PyTorch model work: `<repo-python> -m pip install -c constraints/ci.txt -e ".[dev,pytorch]"`.
- Pipeline example: `<repo-python> -m qlib_platform --config configs/pipeline.yaml init-metadata`; use explicit, validated `--start YYYYMMDD --end YYYYMMDD` windows for backfills.
- Qlib workflow run: use the venv-local `qrun` launcher (`.\.venv\Scripts\qrun.exe` on Windows; `.venv/bin/qrun` on macOS/Linux) with `configs/workflow_lightgbm.yaml` (requires `QLIB_DATA_URI`).
- Tests: `<repo-python> -m pytest` (discovers `tests/test_*.py`).
- Lint/type check: `<repo-python> -m ruff check src tests`, `<repo-python> -m ruff format --check src tests`, and `<repo-python> -m mypy src`.
- Do not use Makefile targets locally until they are parameterized to use `$RepoPython`; they currently resolve Python tools from `PATH`.

## Coding Style & Naming Conventions
- Python 3.10+.
- Use 4-space indentation and keep line length near 110.
- Naming: `snake_case` for functions/modules, `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants.
- Prefer small, composable functions for extraction/normalize/stage steps.
- Store credentials in environment variables, never in code.
- Do not encode a market-data vendor in core package or domain names. Provider SDKs, credentials, rate limits, endpoint translations, and transport-specific behavior belong behind `qlib_platform.data.sources` adapters.
- Do not add new flat implementation modules at `src/qlib_platform/` when a domain package exists. Continue the staged migration toward domain packages; compatibility shims may remain at the root when required by downstream imports.

## Testing Guidelines
- Test framework: `pytest` (declared in optional dev dependencies; invoke it through the repository-local interpreter).
- Keep tests deterministic and lightweight; avoid live vendor/API calls in tests.
- Add/extend tests under `tests/` with file names like `test_<feature>.py`.
- Data-source adapters require contract/registry tests and must be testable without live credentials.
- Run targeted tests with `<repo-python> -m pytest tests/test_normalize.py` before broad runs.
- Before a PR, run the local equivalents of CI quality gates: ruff, mypy, `validate-qrun-contract`, `pytest --cov=src/qlib_platform --cov-report=term-missing --cov-fail-under=60`, and `project-audit --root . --output <temporary-path>`.
- For execution-boundary updates, update `docs/architecture_boundary.md`, `docs/qmt_gateway.md`, and boundary tests together.

## Git Workflow: Trunk-Based Development
- Use `main` plus short-lived task branches. A branch represents one task, never one computer.
- Before starting work on either computer, synchronize `main` with:
  - `git switch main`
  - `git fetch origin`
  - `git pull --ff-only`
- Configure `git config --global pull.ff only` on each development computer. Do not create merge commits merely to synchronize local and remote `main`.
- Direct commits to `main` are allowed only for low-risk changes such as README/comments, spelling, log wording, or a tiny deterministic fix. Run the relevant local checks before pushing.
- Create a short-lived branch and PR for normal features and for all changes involving:
  - research, labels, alpha/features, model selection, or walk-forward logic;
  - data cleaning, point-in-time correctness, calendars, or survivorship handling;
  - backtests, costs, portfolios, strategies, gates, or promotion;
  - production/trading execution;
  - database schemas, CI, dependencies, large refactors, or experimental work once mature.
- Use task-oriented branch names such as `feat/walk-forward-v2`, `fix/qlib-calendar`, `refactor/data-layer`, `chore/update-qlib`, or `docs/research-guide`.
- To continue the same task on another computer, push the task branch and track that same remote branch. Do not restart the task from `main` or create computer-specific branches.
- Keep feature branches current with `git fetch origin` followed by `git rebase origin/main`; do not merge `origin/main` into a feature branch. If the rebased branch was already published, update it with `git push --force-with-lease`, never plain `--force`.
- A feature branch may contain several focused commits. Use `type(scope): concise summary` commit messages, for example `feat(research): add walk-forward runner`.
- Open a PR as the change-set boundary. Before merge:
  - ensure CI passes ruff, mypy, Qlib workflow-contract validation, coverage, project audit, and pytest;
  - review the complete diff, especially correctness and point-in-time effects;
  - document changed stages, commands/test status, config changes, and validation evidence when data or accuracy behavior changes.
- Prefer **Squash and merge** so `main` receives one clean commit per PR. After merge, delete the remote task branch, update local `main` with `git pull --ff-only`, delete the local branch, and run `git fetch --prune`.
- Do not force-push or delete `main`. Repository protection should require PR status checks for critical changes without requiring a self-approval review.
- Agents working in this repository must follow these rules: use a task branch and PR for functional or critical-path changes, and treat direct-to-`main` work as the documented low-risk exception.

## Security & Configuration Tips
- Never commit secrets (`TUSHARE_TOKEN`, `.env`, raw API credentials).
- Execution-state modules for broker/order state are intentionally out-of-repo: do not add order submission, cancellation, replacement, ledger, or broker-state writes here.
- Validate date windows before running full rebuilds; production jobs should run idempotent checks and data-completeness guards.
- Treat `backfill`, `stage-*`, `dump-*`, `daily-sync`, `production-*`, `model-deploy`, `model-rollback`, and scheduled-task installation/removal as state-changing operations. Run them only with explicit user authorization and report affected outputs.


## Repository Invariants
- `qlib-platform` is a Research / Alpha Factory. It consumes immutable `DataRelease` inputs, performs research, and publishes research artifacts through Artifact Contract v2.
- Keep the cross-repository boundary explicit: the sole handoff is a content-addressed `TARGET_PORTFOLIO` bound to exactly one `DataRelease`.
- Do not introduce broker order submission, cancellation, replacement, broker-state writes, OMS ownership, execution ledgers, hard-risk enforcement, or authoritative LEAN execution semantics. Those belong to `platform`.
- Preserve point-in-time causality, immutable artifact identities, per-fold fitted-state isolation, ordered non-overlapping OOS stitching, deterministic lineage/hashes, final-holdout isolation, and fail-closed validation.
- The certified infrastructure baseline is the default explanation for weak research results. Do not alter certified infrastructure behavior merely because a model, alpha, or portfolio result is weak; see `docs/research_infrastructure_certification.md`.
- The active governed program is Phase 3-D. It is diagnostics only: formal candidates, model selection, P2-R01 through P2-R03, final-holdout access, and publishing remain disallowed. Use the `research-diagnostics` Skill and its Phase 3-D profile before Phase 3 work.
