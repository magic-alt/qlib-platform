# Repository Guidelines

## Project Structure & Module Organization
- `src/tushare_qlib/`: Python package (main pipeline, CLI, data processing, model workflow).
- `tests/`: pytest test suites (`test_*.py`).
- `scripts/`: utility entry scripts.
- `configs/`: pipeline and workflow YAML files.
- `data/`: generated/raw/curated output datasets (large artifacts, keep untracked).
- `mlruns/`: experiment metadata/artifacts from training runs.
- `README.md`: operational command reference.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate`
- `pip install --break-system-packages --no-deps -e .[dev]`
- `tq --config configs/pipeline.yaml init-metadata`
- `tq --config configs/pipeline.yaml backfill --start 20250101 --end 20260805`
- `tq --config configs/pipeline.yaml curate`
- `tq --config configs/pipeline.yaml stage-full --force`
- `tq --config configs/pipeline.yaml dump-full`
- `tq --config configs/pipeline.yaml train-select`
- `qrun configs/workflow_lightgbm.yaml` (Qlib workflow run, requires `QLIB_DATA_URI`).
- Tests: `pytest` (discovers `tests/test_*.py`).
- Lint/type check: `ruff check src tests` and `ruff format`/`ruff check`.

## Coding Style & Naming Conventions
- Python 3.10+.
- Use 4-space indentation and keep line length near 110.
- Naming: `snake_case` for functions/modules, `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants.
- Prefer small, composable functions for extraction/normalize/stage steps.
- Store credentials in environment variables, never in code.

## Testing Guidelines
- Test framework: `pytest` (declared in optional dev dependencies).
- Keep tests deterministic and lightweight; avoid live Tushare calls in tests.
- Add/extend tests under `tests/` with file names like `test_<feature>.py`.
- Run targeted tests with `pytest tests/test_normalize.py` before broad runs.

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
  - ensure CI passes `ruff check src tests`, `tq --config configs/pipeline.yaml validate-qrun-contract`, and `pytest`;
  - review the complete diff, especially correctness and point-in-time effects;
  - document changed stages, commands/test status, config changes, and validation evidence when data or accuracy behavior changes.
- Prefer **Squash and merge** so `main` receives one clean commit per PR. After merge, delete the remote task branch, update local `main` with `git pull --ff-only`, delete the local branch, and run `git fetch --prune`.
- Do not force-push or delete `main`. Repository protection should require PR status checks for critical changes without requiring a self-approval review.
- Agents working in this repository must follow these rules: use a task branch and PR for functional or critical-path changes, and treat direct-to-`main` work as the documented low-risk exception.

## Security & Configuration Tips
- Never commit secrets (`TUSHARE_TOKEN`, `.env`, raw API credentials).
- Validate date windows before running full rebuilds; production jobs should run idempotent checks and data-completeness guards.
