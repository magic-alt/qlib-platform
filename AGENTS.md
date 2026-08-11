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

## Commit & Pull Request Guidelines
- Commit history currently shows only an initial commit (`首次提交`), so no strict convention is established yet.
- Recommended format: `type(scope): concise summary` (e.g. `feat(pipeline): add incremental backfill option`).
- PRs should include:
  - a summary of changed pipeline stages,
  - commands executed and test status,
  - sample config changes,
  - validation screenshots/log snippets when data/accuracy behavior changes.

## Security & Configuration Tips
- Never commit secrets (`TUSHARE_TOKEN`, `.env`, raw API credentials).
- Validate date windows before running full rebuilds; production jobs should run idempotent checks and data-completeness guards.
