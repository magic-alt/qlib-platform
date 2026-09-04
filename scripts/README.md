# Repository scripts

Run Python scripts with the repository-local interpreter (`.\.venv\Scripts\python.exe` on
Windows or `.venv/bin/python` on macOS/Linux). These scripts are retained because
they provide an operational entry point, a documented research workflow, or a
repeatable acceptance/build tool that is not duplicated by the main CLI.

## Operations and deployment

- **STATE-CHANGING** `run_tushare_daily_sync.ps1`: run the configured daily sync and write timestamped
  logs under `data/state/daily_sync/logs`.
- **STATE-CHANGING** `register_tushare_daily_sync_task.ps1`: register the Windows scheduled task that
  invokes the daily-sync runner. Supports `-WhatIf` for validation.
- **READ-ONLY RENDER** `render_standalone_scheduler.py`: render the supported systemd or cron scheduler
  assets through `qlib_platform.runtime.scheduler`.
- **ENVIRONMENT-CHANGING** `build_lightgbm_opencl_windows.ps1`: build and verify the pinned native Windows
  LightGBM OpenCL backend using the repository-local Python interpreter.

## Local research quickstart

- **CONVENIENCE WRAPPER** `run_local_research.ps1`: Windows wrapper for the local-data research quickstart.
- **CONVENIENCE WRAPPER** `run_local_research.sh`: macOS/Linux wrapper for the same CLI; invoke with `bash` when the
  checkout does not preserve the executable bit.
- **REPORTING CLI** `<repo-python> -m qlib_platform.research.research_summary <research_matrix.json>`: combine signal and
  prediction-only portfolio evidence into one IC/RankIC/ICIR/RankICIR/ExcessIR/MDD/turnover/cost comparison.

The wrappers do not implement a second research engine. They call
`python -m qlib_platform.research.research_quickstart`, which reuses the existing `bootstrap`, DatasetVersion verification,
`train-select`, `research-run`, `runtime-probe`, and `backtest-predictions` implementations. See
`docs/local_research_quickstart.md`.

The Qlib-native example also has a cross-platform runner at
`examples/local_qlib_backtest/run_backtest.py` plus `run_backtest.sh`; the existing PowerShell runner remains
supported.

## Research and reporting

- **ARTIFACT-WRITING** `export_qrun_backtest_report.py`: convert completed Qlib qrun artifacts into an
  auditable report bundle.
- **ARTIFACT-WRITING** `generate_p0_baseline_artifacts.py`: generate fail-closed P0 reconciliation,
  signal-quality, and cost-stress evidence for a completed run.
- **ARTIFACT-WRITING** `synthesize_p0_orthogonal_audit.py`: combine checksum-backed P0 child audits into
  a fail-closed orthogonal synthesis receipt.

## Acceptance

- **ARTIFACT-WRITING** `compare_full_walk_forward_runs.py`: build the documented full walk-forward
  acceptance evidence for Ridge, LightGBM, and XGBoost runs.
- **TEMPORARY TEST OUTPUT ONLY** `run_cross_repo_golden_acceptance.py`: execute the deterministic mini
  DataRelease-to-Artifact-v2 cross-repository acceptance loop without credentials or
  live market-data access.

TargetPortfolio generation is intentionally not exposed as a standalone script.
Use `<repo-python> -m qlib_platform ... build-target-portfolio`; that CLI performs the
required DataRelease capability check before constructing the governed artifact.
