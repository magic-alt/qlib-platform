# Repository scripts

Run Python scripts with the repository-local interpreter (`.\.venv\python.exe` on
Windows or `.venv/bin/python` on macOS/Linux). These scripts are retained because
they provide an operational entry point, a documented research workflow, or a
repeatable acceptance/build tool that is not duplicated by the main CLI.

## Operations and deployment

- `run_tushare_daily_sync.ps1`: run the configured daily sync and write timestamped
  logs under `data/state/daily_sync/logs`.
- `register_tushare_daily_sync_task.ps1`: register the Windows scheduled task that
  invokes the daily-sync runner. Supports `-WhatIf` for validation.
- `render_standalone_scheduler.py`: render the supported systemd or cron scheduler
  assets through `tushare_qlib.scheduler`.
- `build_lightgbm_opencl_windows.ps1`: build and verify the pinned native Windows
  LightGBM OpenCL backend using the repository-local Python interpreter.

## Research and reporting

- `export_qrun_backtest_report.py`: convert completed Qlib qrun artifacts into an
  auditable report bundle.
- `generate_p0_baseline_artifacts.py`: generate fail-closed P0 reconciliation,
  signal-quality, and cost-stress evidence for a completed run.
- `synthesize_p0_orthogonal_audit.py`: combine checksum-backed P0 child audits into
  a fail-closed orthogonal synthesis receipt.

## Acceptance

- `compare_full_walk_forward_runs.py`: build the documented full walk-forward
  acceptance evidence for Ridge, LightGBM, and XGBoost runs.
- `run_cross_repo_golden_acceptance.py`: execute the deterministic mini
  DataRelease-to-Artifact-v2 cross-repository acceptance loop without credentials or
  live market-data access.

TargetPortfolio generation is intentionally not exposed as a standalone script.
Use `<repo-python> -m tushare_qlib ... build-target-portfolio`; that CLI performs the
required DataRelease capability check before constructing the governed artifact.
