# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

`qlib-platform` is the **Research Plane / Alpha Factory** of a larger institutional
A-share quant system built on Microsoft [Qlib](https://github.com/microsoft/qlib)
(pinned to commit `79633dd`). It ingests data, builds features, trains/evaluates
models, runs walk-forward research, and publishes **research artifacts only**.

It is one half of a two-repo system:

- **`platform` / LEAN** (sibling repo, `../lean-platform`) owns production
  TuShare ingestion, the canonical `DataRelease` publication, authoritative LEAN
  backtests, hard risk, paper/shadow trading, OMS, broker/QMT, orders, fills and
  the ledger.
- **`qlib-platform` (this repo)** consumes an **immutable `DataRelease`**, derives
  Qlib datasets, and publishes up to promotion status **`RESEARCH_PROMOTED`**.

The single integration boundary is a content-addressed `TARGET_PORTFOLIO` inside an
Artifact Contract v2 bundle, bound to one `DataRelease`. **This repo contains no
order-producing, broker-state, or ledger code** — that was physically removed in
Phase 3. Do not add order submission, cancellation, replacement, ledger, or broker
state. See `docs/architecture_boundary.md` and `docs/qmt_gateway.md`. `LEAN_VALIDATED`,
`PAPER`, `PRODUCTION`, `RETIRED` transitions live only in `platform`.

## Environment & how to run commands

Always use the **repository-local interpreter**, never system `python`/`py` or bare
`tq`/`qrun`:

- macOS/Linux: `RepoPython=.venv/bin/python` (there is also a `.venv311`; both are 3.11)
- Windows PowerShell: `$RepoPython = '.\.venv\python.exe'`

If the local interpreter is missing, stop and recreate the venv before proceeding.

```bash
# Install core dev deps (Qlib checkout must be pip-installed into the same venv)
.venv/bin/python -m pip install -c constraints/ci.txt -e ".[dev]"
# Data ingestion deps:  -e ".[all,dev]"
# PyTorch DNN models:   -c constraints/ci.txt -e ".[dev,pytorch]"
# XGBoost profile:      -e ".[xgboost]"  or ".[all,dev]"
```

All pipeline commands go through the module form:

```bash
.venv/bin/python -m tushare_qlib --config <CONFIG> <command> [args]
```

`--config` defaults to `configs/pipeline.yaml`. The Makefile targets exist but
resolve tools from `PATH` and are **not** parameterized to the local interpreter —
run the equivalent commands through `.venv/bin/python` locally instead.

## Quality gates (run before a PR / matches CI)

CI (`.github/workflows/ci.yml`) runs a 3.10/3.11/3.12 × ubuntu/windows matrix plus
a `quality` job and a `dnn-bundle-parity` job. Local equivalents:

```bash
.venv/bin/python -m pytest                                   # discover tests/test_*.py
.venv/bin/python -m pytest tests/test_normalize.py           # single test module
.venv/bin/python -m pytest tests/test_phase3_diagnostics.py::test_name   # single test
.venv/bin/python -m ruff check src tests
.venv/bin/python -m ruff format --check src tests
.venv/bin/python -m mypy src
.venv/bin/python -m tushare_qlib --config configs/pipeline.yaml validate-qrun-contract
.venv/bin/python -m pytest --cov=src/tushare_qlib --cov-report=term-missing --cov-fail-under=60
.venv/bin/python -m tushare_qlib project-audit --root . --output /tmp/project_audit.json
```

Ruff is intentionally narrow (`E4, E7, E9, F`; `E501` ignored, line-length 110).
Tests must be deterministic and **must not make live TuShare calls**.

## Configuration: two profiles, env-var driven

`Settings.load` (`src/tushare_qlib/settings.py`) loads a YAML config, supports
`extends:` inheritance, and expands `${ENV}` placeholders — an unresolved placeholder
**raises** rather than falling back. Pick the profile by data source:

- `configs/pipeline.yaml` — **production research**: `data_source.kind: platform_release`.
  Reads an immutable `DataRelease` from `platform`. Requires env `QUANT_DATA_ROOT`,
  `DATASET_RELEASE_ID` (a `ds_<64-hex>` id), and `QLIB_REPO`. No TuShare token.
- `configs/pipeline_tushare_dev.yaml` — **development / standalone testing only**:
  `data_source.kind: tushare` (or `lean_mysql`). Uses `TUSHARE_TOKEN` from `.env`
  (copy `.env.example` → `.env`). Must **never** be used for production publication.

`source_kind` on `Settings` decides the branch (`uses_platform_release()` vs
`uses_tushare_source()`); ingestion commands are disabled under `platform_release`
because production ingestion belongs to `platform`.

## Architecture: data layering (content-addressed, immutable)

The data platform is described in `docs/qlib_data_platform.md`. Layout under
`project_root` (see `Paths` in `settings.py`):

- `bronze/tushare/{current,revisions}` — raw + content-addressed source partitions
- `silver/daily/current`, `silver/reference/current` — normalized raw-price facts + calendars/master
- `gold/pit/current`, `gold/qlib_staging` — point-in-time financials + Qlib converter working sets
- `qlib/versions/<version_id>` — immutable Qlib datasets
- `registry/qlib.sqlite` — **rebuildable** index of dataset aliases + lineage

Key invariants:

- **Canonical Parquet is the fact layer; Qlib Bin is a versioned, derived view.**
  `current/` dirs are *working views*, not auditable versions.
- A `version_id` is derived from **sorted file checksums + semantic contract +
  declared parents** (build time and filesystem location excluded). Every file is
  checksummed. A failed `dataset-build` leaves the old `research-current` alias
  unchanged.
- **PIT (point-in-time) correctness is central**: financials become visible on the
  first open session strictly after `ann_date`/`f_ann_date`; restatements supersede
  from their own effective session; Qlib prices use a total-return factor anchored
  to the first valid observation. Metadata working views are **atomically replaced**
  so a later refresh can't mutate a hard-linked published snapshot.
- `dataset-build` runs the whole chain (PIT materialization → Silver → Gold → Qlib
  convert → smoke test → immutable publish → alias promote). `curate`/`stage-*`/
  `dump-*`/`dump-update` are low-level recovery tools only; use `dataset-build`
  (full) or `daily-sync` (incremental) for normal publication.

## Architecture: model adapters decouple model from contract

`src/tushare_qlib/models/registry.py` holds a `ModelAdapter` registry
(`ridge`, `lightgbm`, `xgboost`, `pytorch` — see `models/adapters/`). You switch
model families by changing `experiment.model.profile` to a YAML under
`configs/model_profiles/` (e.g. `lightgbm_auto.yaml`, `ridge_golden_v1.yaml`,
`xgboost_cpu_v1.yaml`, `pytorch_mps_m5.yaml`). **The DataRelease, AlphaPack, label,
split and portfolio contracts stay fixed across a model switch** — this is a
certified invariant. `auto` picks only the execution device (CPU/CUDA/OpenCL/MPS),
never the family; a golden CPU baseline is `ridge_golden_v1.yaml`.

## Research flow & gates

The integrated runner `train-select` / `research-run` (in `train_select.py`,
`walk_forward.py`) produces OOS predictions, a research portfolio backtest, a
`strategy_audit.parquet` (candidate→intent→fill→holdings), `timings.json`, MLflow
artifacts, and Markdown/PDF reports. The **Research Gate** (`research_gate.py`,
thresholds in `configs/pipeline.yaml` → `research.promotion_thresholds`) classifies
a run:

- `REJECTED` — hard conditions failed.
- `RESEARCH_REVIEW` — below the line but above a review floor (e.g. `ICIR≥0.30` or
  `Rank ICIR≥0.40`) with all other hard conditions met: keeps evidence/holdout but
  publishes **no** execution candidate.
- Pass — publishes `selection`/`signal`.

For TopkDropout the stability check passes on **`ICIR≥0.50` OR `Rank ICIR≥0.50`**
(ICIR is not the sole veto). `selection_*.csv` is type `MODEL_TOPK` (research TopN
only); the full-score `MODEL_SCORE` file is the only legal input to TopkDropout.
`REJECTED`/missing-lineage/`MODEL_TOPK`-as-portfolio-input all fail closed.

**Walk-forward** default: 1500-day train / 126-day valid / 63-day test, 252 rolling
OOS + independent 252-day final holdout, 6-day purge/embargo/label-buffer. Rolling
folds train+save OOS predictions only; they are stitched by date (no overlap) into
**one continuous account** — no per-fold account backtest and **no fold-boundary
state resets**. The final holdout is a **non-publishing** evaluation behind a
selection lock; only `APPROVED_RECIPE` (after a production refit) is publishable.
Full acceptance protocol: `docs/full_walk_forward_acceptance.md`.

**Alpha Research Phases** are a gated, mostly-frozen pipeline
(`src/tushare_qlib/research/`, commands `phase{1,2,3}-*`): Phase 1 synthesis,
Phase 2 evidence collection + hypothesis binding, Phase 3 (active) temporal alpha
stability & regime diagnosis. Each phase is entered via a `*-contract-lock` JSON that
freezes the contract. **Research Infrastructure is CERTIFIED** (see
`docs/research_infrastructure_certification.md`): a weak/`REJECTED` result is **not**
by itself evidence of a data/infra bug — attribute weak performance to features,
regime, model fit, or portfolio **before** reopening infrastructure diagnosis.

## Windows multiprocessing gotcha

When `qlib_kernels > 1`, Qlib uses Joblib `loky`; stdin/pipe (`python -`) and
`python -c` fail before Qlib init on Windows `spawn`. Launch multi-process research
via `python -m tushare_qlib` or a `.py` guarded by `if __name__ == "__main__":`.
`qlib_kernels: 1` is only for short-window isolated diagnostics.

## Git workflow (trunk-based, from AGENTS.md)

- Trunk `main` + short-lived **task branches** (one task, not one computer).
  Task branches that touch research, labels, PIT/survivorship, backtests, costs,
  portfolio, execution, DB schema, CI, deps, or large refactors **must** go through a
  PR. Low-risk changes (README/comments/typos, tiny deterministic fixes) may commit
  direct to `main` after local checks.
- Branch names: `feat/…`, `fix/…`, `refactor/…`, `chore/…`, `docs/…`.
- Keep branches current with `git fetch` + `git rebase origin/main`; rebase-published
  branches use `git push --force-with-lease` (never plain `--force`). Never force-push
  or delete `main`. Prefer **squash merge**; one clean commit per PR on `main`.
- Commit messages: `type(scope): summary`, e.g. `feat(research): add walk-forward runner`.
- If you change an execution boundary, update `docs/architecture_boundary.md`,
  `docs/qmt_gateway.md`, and the boundary tests together.

## State-changing operations — require explicit user authorization

Treat these as mutating and report affected outputs; run only when authorized:
`backfill`, `backfill-extended`, `curate`/`curate-day`, `stage-*`, `dump-*`,
`daily-sync`, `migrate-qlib-layout`, `model-deploy`, `model-rollback`, and
scheduled-task install/remove (PowerShell scripts in `scripts/`). `daily-sync`
unifies open-auction, corporate actions, metadata, incremental staging, Qlib publish,
and `research-current` alias update for an `--as-of` business date.

## Layout cheat-sheet

- `src/tushare_qlib/` — the package: `cli.py` (argparse dispatch, the source of
  truth for every command), `settings.py` (config + paths), `models/` (adapters +
  registry), `research/` (phased studies + diagnostics), plus data layers
  (`extract`, `normalize`, `fundamentals`, `qlib_export`), `dataset_*` (registry/
  resolver/manifest), `walk_forward*`, `train_select`, `research_gate`, `artifacts`/
  `institutional_artifacts`/`research_bundle_export` (Artifact Contract v2),
  `lean_bridge`/`lean_integration`, `daily_sync`/`live_inference`, `model_registry`/
  `production_refit`.
- `configs/` — `pipeline.yaml` (prod), `pipeline_tushare_dev.yaml` (dev),
  `model_profiles/`, `research/`, `regimes/`, `alpha_taxonomy/`, `workflow_lightgbm.yaml`.
- `contracts/` — **vendored** Artifact Contract v2 JSON schemas from `platform`
  (source rev `fd56480`); update both schema files and their tests together.
- `scripts/` — operational entry scripts incl. `run_cross_repo_golden_acceptance.py`
  (isolated cross-repo PASS/`LEAN_VALIDATED` check, no external data source).
- `tests/` (~89 `test_*.py`), `docs/` (runbooks), `mlruns/` + `data/` (large,
  untracked artifacts; `data` is a symlink to the lean-platform data root).
