---
status: ACTIVE
owner: research
applies_to_commit: 0ff8bf4d443a3f5c8e864d73f7d9fbd0bb778134
last_verified: 2026-09-04
---

# Local Research Quickstart

This guide is the supported shortest path from an existing local `data/` tree or Qlib provider to Alpha158,
multi-model comparison, prediction-only portfolio simulation, and walk-forward research.

The quickstart is deliberately a **thin orchestration layer**. It does not replace `DataRelease`, `DatasetVersion`,
`train-select`, `research-run`, `backtest-predictions`, Feature Store, ModelAdapter, or Qlib. Those existing components
remain the authoritative implementations.

> [!IMPORTANT]
> The convenience commands do not override [Current Governance State](current_state.md). In particular, the existence
> of a generic research command never authorizes formal candidate creation, sealed-holdout access, model selection,
> research promotion, or publishing.

## What was added

The repository now has one discoverable local-research entry point:

```text
<repo-python> -m tushare_qlib.research_quickstart COMMAND
```

After editable/wheel installation, the equivalent console entry point is `tq-research`. Repository development and
runbooks should still prefer the repository-local interpreter.

The main commands are:

| Command | Purpose |
| --- | --- |
| `doctor` | Detect local data state, resolve/verify DatasetVersion, inspect AlphaPack/model readiness |
| `prepare` | Reuse `bootstrap` to import/build/download the selected local data source |
| `catalog` | List AlphaPacks and built-in model presets |
| `plan` | Materialize generated AlphaPack overlays and exact commands without training |
| `run` | Run one or more AlphaPack/model experiments |
| `matrix` | Run the default Alpha158 Market/Daily/PIT × Ridge/LightGBM/XGBoost matrix |
| `backtest` | Delegate to the prediction-only portfolio backtest without fitting a model |

A separate comparison command summarizes completed matrix evidence:

```text
<repo-python> -m tushare_qlib.research_summary <research_matrix.json>
```

It produces IC, RankIC, ICIR, RankICIR, ExcessIR, max drawdown, turnover when available, and cost in one table without
silently inventing missing metrics.

## Entry-point selection

Use the three layers for different goals:

| Goal | Recommended entry point |
| --- | --- |
| First local experiment / model matrix | `scripts/run_local_research.ps1` or `scripts/run_local_research.sh` |
| Exact governed research primitive | `<repo-python> -m tushare_qlib ...` |
| Learn upstream Qlib workflow YAML / custom Qlib Model | `examples/local_qlib_backtest/` |

The quickstart launches the existing repository CLI as subprocesses. There is one DatasetVersion verifier, one
`train-select` implementation, one walk-forward implementation, and one prediction-only backtest implementation.

## Install

From the repository root, create the repository-local Python 3.12 environment.

### Windows PowerShell

```powershell
python3.12 -m venv .venv
$RepoPython = '.\.venv\Scripts\python.exe'
& $RepoPython -m pip install --upgrade pip
& $RepoPython -m pip install -c constraints\ci.txt -e ".[all,dev]"
```

### macOS / Linux

```bash
python3.12 -m venv .venv
RepoPython=.venv/bin/python
$RepoPython -m pip install --upgrade pip
$RepoPython -m pip install -c constraints/ci.txt -e '.[all,dev]'
```

`all` includes Qlib, LightGBM and XGBoost but intentionally does **not** include PyTorch. Add PyTorch only when needed:

```bash
$RepoPython -m pip install -c constraints/ci.txt -e '.[dev,pytorch]'
```

## End-to-end research path

The recommended progression is:

```text
local data
  -> doctor
  -> prepare/import/build when required
  -> deep DatasetVersion verification
  -> AlphaPack/model catalog
  -> optional qrun smoke test
  -> fixed OOS signal screen
  -> prediction-only portfolio backtest
  -> compare IC/RankIC/ICIR/RankICIR + ExcessIR/MDD/turnover/cost
  -> freeze the intended exploratory recipe
  -> walk-forward rolling OOS
  -> separately governed candidate/holdout lifecycle when authorized
```

This is the convenience form of the existing lower-level sequence:

```text
dataset-list
  -> dataset-resolve
  -> dataset-verify --mode deep
  -> feature-store (optional explicit pre-materialization)
  -> train-select / research-run
  -> backtest-predictions
  -> reports / diagnostics
```

## 1. Inspect the existing `data/`

Windows:

```powershell
.\scripts\run_local_research.ps1 doctor
```

macOS/Linux:

```bash
bash scripts/run_local_research.sh doctor
```

The source resolver can report:

- `READY` — the active local DatasetVersion is usable;
- `IMPORT_REQUIRED` — a Qlib-shaped provider exists but has not been frozen/imported;
- `BUILD_REQUIRED` — local canonical/raw data exists and needs a release/dataset build;
- `MATERIALIZE_REQUIRED` — a release exists but the intended active dataset still needs explicit materialization;
- `DOWNLOAD_REQUIRED` — TuShare download is the selected fallback and credentials are available;
- `DATA_INCOMPLETE` / `DATA_UNAVAILABLE` — required inputs are missing;
- `RELEASE_SELECTION_REQUIRED` — multiple releases exist without an active selection.

When the source is `READY`, `doctor` resolves the configured alias, uses bounded deterministic sampled verification by
default, checks AlphaPack compatibility as far as the DatasetVersion contract permits, and probes
Ridge/LightGBM/XGBoost/PyTorch runtime availability. Use `--verify-mode deep` when you explicitly want a full diagnostic
checksum pass.

The standalone default alias is normally `standalone-current`. The TuShare development profile uses
`research-current`.

For exact low-level inspection:

```powershell
& $RepoPython -m tushare_qlib dataset-list
& $RepoPython -m tushare_qlib dataset-resolve standalone-current
& $RepoPython -m tushare_qlib dataset-verify standalone-current --mode deep
```

> [!NOTE]
> An imported legacy Qlib provider may not declare enough fields/components in its manifest to prove that PIT or
> fundamental AlphaPacks are available. Treat a missing compatibility contract as insufficient evidence; execution
> remains fail-closed when required fields are actually accessed.

## 2. Prepare/import/build local data

### Automatic bootstrap

```powershell
.\scripts\run_local_research.ps1 prepare --source auto
```

This reuses the existing `bootstrap` implementation. It never creates a parallel data lifecycle. If the resolver says
that an immutable release needs an explicit selection/materialization step, follow the returned action rather than
silently choosing one of several releases.

`doctor` and `prepare` default to bounded sampled verification so discovery/bootstrap does not repeatedly hash an entire
large local provider. `run` and `matrix` retain `deep` as their default pre-research verification tier. Any quickstart
command can still request full verification explicitly with `--verify-mode deep`.

### Existing Qlib binary provider

```powershell
.\scripts\run_local_research.ps1 prepare --source qlib --path D:\quant\cn_data
```

macOS/Linux:

```bash
bash scripts/run_local_research.sh prepare --source qlib --path /data/qlib/cn_data
```

Equivalent low-level command:

```powershell
& $RepoPython -m tushare_qlib release import-qlib --path D:\quant\cn_data
```

The import freezes the provider into an immutable exploratory DataRelease/DatasetVersion and moves the configured local
aliases only after registration. Importing OHLCV does not manufacture PIT fundamentals.

### Existing local raw/canonical data

```powershell
.\scripts\run_local_research.ps1 prepare --source raw --start 20160104 --end 20260810
```

The existing bootstrap path decides whether the available components satisfy a market-import profile or the complete
research build.

### TuShare bootstrap

```powershell
.\scripts\run_local_research.ps1 prepare --source tushare --start 20160104 --end 20260810
```

This requires `TUSHARE_TOKEN` and delegates to the current ingestion/bootstrap chain.

### Explicit full dataset build

When you intentionally need the low-level builder:

```powershell
& $RepoPython -m tushare_qlib dataset-build --start 20160104 --end 20260810
```

Do not rebuild merely because a model result is weak.

## 3. AlphaPack catalog

```powershell
.\scripts\run_local_research.ps1 catalog
```

Current research packs include:

| AlphaPack | Primary use | Lookback |
| --- | --- | ---: |
| `alpha158_market_v1` | OHLCV / technical Alpha158 baseline | 60 |
| `alpha158_daily_v1` | Alpha158 + liquidity/valuation/state daily fields | 60 |
| `alpha158_pit_v1` | Alpha158 + PIT fundamental fields | 60 |
| `multifactor_core_v1` | momentum/value/quality/growth/size/liquidity factor research | 60 |
| `ashare_factor_benchmark_v1` | broader benchmark factor pack | 252 |
| `ashare_alpha_phase2_v1` | frozen Phase-2 feature contract | 252 |

A practical escalation order is:

```text
alpha158_market_v1
  -> alpha158_daily_v1
  -> alpha158_pit_v1
  -> multifactor_core_v1
```

Start with `alpha158_market_v1` for an arbitrary imported Qlib provider. Move to PIT packs only after the data contract
supports their required fields/components.

## 4. Model presets and runtime probes

The quickstart recognizes:

| Name | Profile | Device behavior |
| --- | --- | --- |
| `ridge` | `ridge_golden_v1.yaml` | CPU baseline |
| `lightgbm` | `lightgbm_auto.yaml` | repository LightGBM runtime resolution |
| `xgboost` | `xgboost_cpu_v1.yaml` | CPU |
| `pytorch` | `pytorch_auto.yaml` | CUDA -> MPS -> CPU automatic resolution |

Every quickstart job runs the existing `runtime-probe` before training. Probe explicitly with:

```powershell
& $RepoPython -m tushare_qlib runtime-probe --model-profile configs/model_profiles/lightgbm_auto.yaml
& $RepoPython -m tushare_qlib runtime-probe --model-profile configs/model_profiles/pytorch_auto.yaml
```

The portable PyTorch profile is particularly useful on Apple Silicon: MPS is selected when available, while NVIDIA
hosts resolve to CUDA and CPU-only hosts record the fallback.

## 5. First Alpha158 experiment

Windows:

```powershell
.\scripts\run_local_research.ps1 run --alpha-pack alpha158_market_v1 --model lightgbm
```

macOS/Linux:

```bash
bash scripts/run_local_research.sh run --alpha-pack alpha158_market_v1 --model lightgbm
```

The fixed-mode default is intentionally conservative:

```text
train-select --stage signal
  -> PredictionSnapshot
  -> backtest-predictions
```

The first stage measures signal quality without model promotion. The second consumes the immutable OOS predictions and
runs the Qlib portfolio simulator without feature computation, model fitting, or model prediction.

Use signal-only mode when portfolio conversion is not needed:

```powershell
.\scripts\run_local_research.ps1 run --model lightgbm --no-prediction-backtest
```

## 6. Freeze explicit train/valid/test windows

For controlled model comparison, set all three windows together:

```powershell
.\scripts\run_local_research.ps1 run `
  --alpha-pack alpha158_pit_v1 `
  --model lightgbm `
  --train 2018-10-01 2024-12-27 `
  --valid 2025-01-08 2025-07-02 `
  --test 2025-07-11 2026-08-10
```

The wrapper rejects partial explicit splits. The underlying `train-select` still validates strict chronological,
non-overlapping windows. Do not extend the test end date after looking at its performance and continue calling the same
experiment “OOS”.

## 7. Default Alpha158 × model matrix

Run the first broad comparison with one command:

```powershell
.\scripts\run_local_research.ps1 matrix
```

Default matrix:

```text
Alpha158 Market  × Ridge / LightGBM / XGBoost
Alpha158 Daily   × Ridge / LightGBM / XGBoost
Alpha158 PIT     × Ridge / LightGBM / XGBoost
```

Each job uses a generated overlay that only changes `experiment.alpha.pack` and extends the selected base config. It
does not silently change DatasetVersion, label, split rules, universe, costs, strategy, or portfolio constraints.

Outputs are written under:

```text
data/output/quickstart/<timestamp>-matrix/
  configs/
    alpha158_market_v1.yaml
    alpha158_daily_v1.yaml
    alpha158_pit_v1.yaml
  research_matrix.json
  research_matrix.md
```

Add PyTorch explicitly:
```powershell
.\scripts\run_local_research.ps1 matrix `
  --model ridge --model lightgbm --model xgboost --model pytorch
```

Compare only one pack:

```powershell
.\scripts\run_local_research.ps1 matrix `
  --alpha-pack alpha158_pit_v1 `
  --model ridge --model lightgbm --model xgboost
```

Run `multifactor_core_v1` with the same interface:

```powershell
.\scripts\run_local_research.ps1 run --alpha-pack multifactor_core_v1 --model lightgbm
```

## 8. Compare IC/RankIC and portfolio economics

After a completed quickstart matrix, generate the comparison table:

```powershell
& $RepoPython -m tushare_qlib.research_summary `
  data\output\quickstart\<RUN>\research_matrix.json
```

macOS/Linux:

```bash
$RepoPython -m tushare_qlib.research_summary \
  data/output/quickstart/<RUN>/research_matrix.json
```

This writes:

```text
research_comparison.json
research_comparison.md
```

The table combines:

1. `IC` / `RankIC`;
2. `ICIR` / `RankICIR`;
3. `ExcessIR`;
4. maximum drawdown;
5. mean and total turnover **only when Qlib's portfolio report exposes a turnover field**;
6. transaction cost evidence;
7. research and portfolio manifest paths.

For signal-stage runs, signal metrics come from the research manifest and portfolio metrics come from the separate
prediction-only backtest. For full evaluation/walk-forward manifests that already contain governed portfolio metrics,
the summarizer uses those and only recomputes missing ExcessIR/MDD from `portfolio_report.parquet`.

Missing turnover is left blank; it is never fabricated from an arbitrary proxy.

## 9. Full fixed evaluation

When you explicitly intend to run the normal full fixed gate/evaluation path:

```powershell
.\scripts\run_local_research.ps1 run `
  --alpha-pack alpha158_pit_v1 `
  --model lightgbm `
  --stage release `
  --train <START> <END> `
  --valid <START> <END> `
  --test <START> <END>
```

This delegates to `train-select --stage release`, which carries the repository's normal promotion/gate semantics.
Check [Current Governance State](current_state.md) before using this mode in a governed program. The quickstart itself
does not weaken or bypass those rules.

## 10. Walk-forward rolling OOS

After narrowing the exploratory recipe, run rolling OOS:

```powershell
.\scripts\run_local_research.ps1 run `
  --mode walk-forward `
  --alpha-pack alpha158_pit_v1 `
  --model lightgbm `
  --start 2019-01-01 `
  --end 2026-08-10
```

Or compare several models:

```powershell
.\scripts\run_local_research.ps1 matrix `
  --mode walk-forward `
  --alpha-pack alpha158_pit_v1 `
  --model ridge --model lightgbm --model xgboost `
  --start 2019-01-01 --end 2026-08-10
```

The wrapper delegates to the existing:

```text
research-run --mode walk-forward --stage release
```

and gives every AlphaPack/model pair its own checkpoint namespace. The current walk-forward implementation continues to
own fitted-state isolation, OOS stitching, checkpoint validation, and continuous predictions-only portfolio behavior.

A quickstart matrix is **not** a formal hypothesis/candidate lock. When an active governed research phase authorizes a
formal hypothesis workflow, use that phase's contract/lock commands rather than treating a convenient local matrix as
approval evidence.

## 11. Plan or dry-run before expensive work

Write the exact generated overlays and commands without training:

```powershell
.\scripts\run_local_research.ps1 plan `
  --alpha-pack alpha158_pit_v1 `
  --model ridge --model lightgbm `
  --output data\output\plans\alpha158_compare
```

`run` and `matrix` also support `--dry-run`.

Use this before a large PyTorch or walk-forward matrix so date windows, DatasetVersion, AlphaPack and model profiles can
be reviewed before compute begins.

## 12. Feature Store

The quickstart does not create a second cache. `train-select` and `research-run` use the Feature Store configuration in
the selected pipeline profile.

Explicit pre-materialization remains available:

```powershell
& $RepoPython -m tushare_qlib feature-store `
  --dataset-ref standalone-current `
  --start 2018-10-01 `
  --end 2026-08-10
```

Use `--force` only for an intentional rebuild under the existing feature contract.

## 13. Prediction-only portfolio backtest

Run directly from an OOS prediction Parquet or PredictionSnapshot:

```powershell
.\scripts\run_local_research.ps1 backtest `
  data\output\research\<RUN_ID>\oos_predictions.parquet `
  --dataset-ref standalone-current `
  --artifact-level full
```

This delegates to `backtest-predictions`. It does not train or predict a model and marks the result research-only.

## 14. Direct Qlib `qrun`

The maintained Qlib-native example remains available when the goal is to understand Qlib YAML or write a raw Qlib
`Model` plugin.

### Windows

```powershell
.\examples\local_qlib_backtest\run_backtest.ps1 -Model lightgbm
.\examples\local_qlib_backtest\run_backtest.ps1 -Model ridge
.\examples\local_qlib_backtest\run_backtest.ps1 -Model custom_ridge
```

### Cross-platform Python

```bash
$RepoPython examples/local_qlib_backtest/run_backtest.py --model lightgbm
$RepoPython examples/local_qlib_backtest/run_backtest.py --model ridge
$RepoPython examples/local_qlib_backtest/run_backtest.py --model custom_ridge
```

### macOS/Linux wrapper

```bash
bash examples/local_qlib_backtest/run_backtest.sh --model lightgbm
```

The cross-platform runner mirrors the PowerShell safety path:

```text
dataset-resolve
  -> dataset-verify --mode deep
  -> bind immutable QLIB_DATA_URI
  -> validate-qrun-contract
  -> venv-local qrun
```

Use the qrun example for Qlib-native education/plugins. Use the integrated quickstart/`train-select`/`research-run`
paths when you need qlib-platform research evidence and lineage.

## 15. Custom Qlib `Model`

The working plugin example is:

```text
examples/local_qlib_backtest/custom_model.py
examples/local_qlib_backtest/workflow_custom_ridge.yaml
```

A Qlib plugin should:

- inherit `qlib.model.base.Model`;
- read only train/valid data permitted by the experiment in `fit()`;
- never read `test` during fitting/selection;
- keep fitted state serializable by the Qlib recorder;
- preserve the `datetime/instrument` inference index.

Run the template with the cross-platform command above and then replace only the estimator/model implementation.

## 16. Custom qlib-platform `ModelAdapter`

An integrated model family is different from a raw Qlib `Model` plugin. Implement
`tushare_qlib.models.base.ModelAdapter` and register it with `tushare_qlib.models.registry`.

A complete adapter owns:

- allowed device validation;
- runtime resolution;
- profile-to-model parameter translation;
- model construction;
- model bundle save/load;
- loaded-model prediction/parity behavior;
- final-refit behavior where applicable.

After registration, create a model profile YAML and pass it directly:

```powershell
.\scripts\run_local_research.ps1 run `
  --alpha-pack alpha158_pit_v1 `
  --model-profile configs/model_profiles/my_adapter.yaml
```

Do not add hidden estimator switches to the quickstart. Algorithm-specific behavior belongs in the ModelAdapter.

## 17. Output directories

### Quickstart orchestration

```text
data/output/quickstart/<timestamp>-<command>/
  configs/*.yaml
  research_matrix.json
  research_matrix.md
  research_comparison.json       # after research_summary
  research_comparison.md         # after research_summary
```

### Integrated research runs

```text
data/output/research/<RUN_ID>/
  manifest.json
  oos_predictions.parquet
  oos_predictions.snapshot.json
  oos_labels.parquet
  portfolio_report.parquet       # portfolio/full runs
  strategy_audit.parquet         # portfolio/full runs
  holdings.parquet               # portfolio/full runs
  research_gate.json             # full fixed evaluation
  timings.json
  backtest_report.md
  backtest_report.pdf
```

### Qlib-native examples

```text
mlruns/examples_local_backtest/
```

The matrix/comparison files are navigation and comparison evidence. Each run's own `manifest.json` remains authoritative
for its identity, runtime, lineage, metrics, gate/promotion state and artifact paths.

## 18. Windows command sheet

```powershell
$RepoPython = '.\.venv\Scripts\python.exe'
& $RepoPython -m pip install -c constraints\ci.txt -e ".[all,dev]"

.\scripts\run_local_research.ps1 doctor
.\scripts\run_local_research.ps1 prepare --source auto
.\scripts\run_local_research.ps1 catalog
.\scripts\run_local_research.ps1 run --alpha-pack alpha158_market_v1 --model lightgbm
.\scripts\run_local_research.ps1 matrix

# Compare the completed matrix
& $RepoPython -m tushare_qlib.research_summary data\output\quickstart\<RUN>\research_matrix.json

# Walk-forward after narrowing the recipe
.\scripts\run_local_research.ps1 run --mode walk-forward --alpha-pack alpha158_pit_v1 --model lightgbm
```

PyTorch:

```powershell
& $RepoPython -m pip install -c constraints\ci.txt -e ".[dev,pytorch]"
.\scripts\run_local_research.ps1 run --alpha-pack alpha158_pit_v1 --model pytorch
```

## 19. Apple Silicon / M5 command sheet

```bash
RepoPython=.venv/bin/python
$RepoPython -m pip install -c constraints/ci.txt -e '.[all,dev]'

bash scripts/run_local_research.sh doctor
bash scripts/run_local_research.sh prepare --source auto
bash scripts/run_local_research.sh matrix
```

Add PyTorch and verify MPS resolution:

```bash
$RepoPython -m pip install -c constraints/ci.txt -e '.[dev,pytorch]'
$RepoPython -m tushare_qlib runtime-probe --model-profile configs/model_profiles/pytorch_auto.yaml
bash scripts/run_local_research.sh run --alpha-pack alpha158_pit_v1 --model pytorch
```

If MPS is not available, the auto profile records the fallback instead of claiming acceleration.

For LightGBM CPU experiments on an M-series Mac, select the explicit `lightgbm_cpu_m5.yaml` profile when you want to
freeze that runtime recipe:

```bash
bash scripts/run_local_research.sh run \
  --alpha-pack alpha158_pit_v1 \
  --model-profile configs/model_profiles/lightgbm_cpu_m5.yaml
```

## 20. Troubleshooting

### `doctor` reports `IMPORT_REQUIRED`

Freeze the intended provider explicitly:

```powershell
.\scripts\run_local_research.ps1 prepare --source qlib --path <QLIB_PROVIDER>
```

### `RELEASE_SELECTION_REQUIRED`

Do not let the convenience layer choose among multiple immutable releases. Inspect and promote deliberately:

```powershell
& $RepoPython -m tushare_qlib release list
& $RepoPython -m tushare_qlib release promote <DATA_RELEASE_ID> --alias research-release-current
```

Then resolve/materialize the intended DatasetVersion.

### Dataset verification fails

Do not point Qlib at an unverified mutable directory to bypass the failure. Repair/reimport/rebuild the source and rerun
`dataset-verify --mode deep`.

### Alpha158 Market works but Daily/PIT fails

The DatasetVersion probably does not contain the additional daily/PIT fields or components. Inspect the DatasetVersion
manifest and use a capable local research release rather than weakening the AlphaPack contract.

### PyTorch is unavailable

Install `.[dev,pytorch]`, then run `runtime-probe`. An explicitly requested MPS/CUDA profile fails when the backend is not
available; `pytorch_auto` may fall back to another supported device.

### LightGBM/XGBoost differs across machines

Compare DatasetVersion, FeatureSnapshot, model profile fingerprint, dependency versions, resolved device, random seed,
thread count, AlphaPack, label and split before attributing the difference to alpha.

### Fixed split is rejected

If any explicit split is supplied, all of `--train`, `--valid`, and `--test` are required. This prevents one user-defined
window from being mixed with automatically derived windows.

### Walk-forward is too expensive

Run `plan`/`--dry-run`, narrow the fixed model matrix first, and reuse the governed FeatureSnapshot/checkpoints. Do not
reduce purge/embargo or alter holdout boundaries merely to make the run faster.

### Comparison has blank turnover

The summarizer reports turnover only when the Qlib portfolio report actually exposes a `turnover` column. A blank value
means the evidence did not support that metric; it is intentionally not replaced by an invented proxy.

## Recommended progression

For a new local installation:

```text
1. doctor
2. prepare --source auto                # only when needed
3. catalog
4. Alpha158 Market + Ridge/LightGBM
5. Alpha158 Daily + Ridge/LightGBM/XGBoost
6. Alpha158 PIT + Ridge/LightGBM/XGBoost
7. Optional PyTorch after simpler baselines
8. research_summary: IC/RankIC/ICIR/RankICIR + ExcessIR/MDD/turnover/cost
9. Review/freeze the intended exploratory data/alpha/label/split/model/portfolio recipe
10. walk-forward rolling OOS
11. Enter phase-specific candidate/holdout workflows only when current governance explicitly authorizes them
```

This ordering separates four questions that are often mixed together: whether local data is usable, whether the alpha
contains information, whether model complexity adds out-of-sample value, and whether the signal survives portfolio
conversion/costs over time.
