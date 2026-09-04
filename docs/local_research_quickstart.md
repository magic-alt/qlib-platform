---
status: ACTIVE
owner: research
last_verified: 2026-09-04
---

# Local Research Quickstart

This is the supported shortest path from a fresh checkout, an existing `data/` tree, or an existing Qlib provider to Alpha158 research.

The standalone contract is intentionally simple:

> **Configure `.env`; do not configure DataRelease IDs, DatasetVersion IDs, aliases, or YAML for normal local research.**

`DataRelease` and `DatasetVersion` remain immutable internal identities for reproducibility, but the quickstart owns their normal lifecycle.

## 1. Install

Python `>=3.10,<3.13` is supported; Python 3.12 is recommended.

### macOS / Linux

```bash
git clone https://github.com/magic-alt/qlib-platform.git
cd qlib-platform
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -c constraints/ci.txt -e '.[all,dev]'
cp .env.example .env
```

### Windows PowerShell

```powershell
git clone https://github.com/magic-alt/qlib-platform.git
cd qlib-platform
python3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -c constraints\ci.txt -e ".[all,dev]"
Copy-Item .env.example .env
```

The copied `.env` is valid as-is for standalone local research:

```dotenv
QLIB_DATA_ROOT=./data
TUSHARE_CALLS_PER_MINUTE=180
TUSHARE_TOKEN=
QLIB_REPO=
QLIB_DATA_URI=
```

Only fill optional values when needed:

- `TUSHARE_TOKEN`: this machine must download or refresh TuShare data;
- `QLIB_REPO`: optional Qlib source-checkout override;
- `QLIB_DATA_URI`: optional existing Qlib provider override;
- integrated-mode variables are not required for standalone research.

No `ds_*` identifier belongs in `.env` for standalone use.

## 2. Run the first study

### macOS / Linux

```bash
bash scripts/run_local_research.sh run \
  --alpha-pack alpha158_market_v1 \
  --model lightgbm
```

### Windows

```powershell
.\scripts\run_local_research.ps1 run --alpha-pack alpha158_market_v1 --model lightgbm
```

That is the normal entry point. A separate `doctor`, `prepare`, `release promote`, `dataset-build`, or registry command is **not** a prerequisite.

On the default `standalone-current` reference, `run` automatically performs the safe preparation needed before training:

```text
.env
  -> resolve existing DatasetVersion if READY
  -> otherwise resolve local source
       -> existing Qlib provider: freeze/import
       -> compatible DataRelease: materialize exactly that frozen release
       -> local raw data: build the supported release/dataset
       -> TuShare: download only when token/config permits
  -> verify DatasetVersion
  -> Alpha158
  -> LightGBM
  -> OOS PredictionSnapshot
  -> prediction-only Qlib backtest
  -> research artifacts
```

The default fixed-mode run uses `train-select --stage signal`; it measures signal quality without promoting a model.

## 3. What happens to DataRelease and DatasetVersion?

Standalone mode deliberately separates **user configuration** from **internal reproducibility identity**.

Normal user-facing selectors are stable aliases:

```text
research-release-current   active immutable DataRelease
standalone-current         active immutable Qlib DatasetVersion
```

The quickstart manages both aliases atomically after successful verification/materialization.

### Multiple `ds_*` releases

Standalone mode no longer asks the user to choose among content hashes.

If several active local DataReleases exist, the resolver:

1. filters out profiles that cannot materialize a Qlib research dataset;
2. selects the newest compatible release by publication/as-of time;
3. verifies/materializes it;
4. promotes the matching release + DatasetVersion snapshot;
5. keeps one active release by default;
6. moves older immutable releases to `data/releases/archive/`.

Archived releases are not deleted. Exact immutable IDs remain addressable for audit/replay, while `release list` stays useful for normal operation.

This policy is controlled by the standalone profile's `release_store.active_keep: 1`. Advanced/integrated workflows can use a different policy explicitly.

### `MATERIALIZE_REQUIRED`

`MATERIALIZE_REQUIRED` is an internal transition, not a normal operator task.

For a compatible frozen DataRelease the bootstrap now reconstructs `qlib_staging` and PIT universe data from the release itself, then creates a DatasetVersion whose manifest remains bound to that release. It does **not** silently rebuild a different release from mutable raw data.

That distinction preserves lineage:

```text
selected DataRelease
       |
       +-- frozen qlib_staging / PIT inputs
       v
DatasetVersion
       |
       v
standalone-current
```

## 4. Optional diagnostics

`doctor` is read-only and useful when troubleshooting:

```bash
bash scripts/run_local_research.sh doctor
```

or on Windows:

```powershell
.\scripts\run_local_research.ps1 doctor
```

It reports source state, verifies the current DatasetVersion with bounded sampled verification, checks AlphaPack compatibility, and probes model runtimes.

Typical states:

| State | Meaning | Normal action |
| --- | --- | --- |
| `READY` | active DatasetVersion is usable | run research |
| `IMPORT_REQUIRED` | existing Qlib provider needs freezing | `run` auto-prepares |
| `BUILD_REQUIRED` | local raw/canonical data needs a build | `run` auto-prepares |
| `MATERIALIZE_REQUIRED` | compatible frozen release needs a DatasetVersion | `run` auto-materializes |
| `DOWNLOAD_REQUIRED` | TuShare is the available fallback | configure `TUSHARE_TOKEN`, then run |
| `DATA_INCOMPATIBLE` | releases exist but cannot materialize requested standalone research | provide capable local data/release |
| `DATA_INCOMPLETE` | local raw inputs are incomplete | provide missing data or use another source |
| `DATA_UNAVAILABLE` | no usable source exists | add local data/provider or configure TuShare |

`RELEASE_SELECTION_REQUIRED` remains an intentional fail-closed state for **integrated/advanced** workflows where selecting between historical immutable releases is an operator decision. It is not part of the normal standalone path.

## 5. Optional explicit preparation

You can still run preparation separately for diagnostics or automation:

```bash
bash scripts/run_local_research.sh prepare --source auto
```

Existing Qlib provider:

```bash
bash scripts/run_local_research.sh prepare --source qlib --path /data/qlib/cn_data
```

Windows:

```powershell
.\scripts\run_local_research.ps1 prepare --source qlib --path D:\quant\cn_data
```

Explicit TuShare bootstrap:

```bash
bash scripts/run_local_research.sh prepare --source tushare --start 20160104 --end 20260810
```

The last command requires `TUSHARE_TOKEN` in `.env`.

Low-level lifecycle commands (`release`, `dataset-build`, `dataset-promote`, `registry-rebuild`) remain available for maintenance and integrated pipelines; they are not onboarding steps.

## 6. AlphaPack catalog

```bash
bash scripts/run_local_research.sh catalog
```

| AlphaPack | Intended data | Use |
| --- | --- | --- |
| `alpha158_market_v1` | OHLCV/basic market fields | safest baseline |
| `alpha158_daily_v1` | market + liquidity/valuation/state | richer daily research |
| `alpha158_pit_v1` | daily + PIT fundamentals | PIT Alpha158 |
| `multifactor_core_v1` | PIT fundamentals + PIT industry | multifactor research |

Recommended escalation:

```text
alpha158_market_v1
  -> alpha158_daily_v1
  -> alpha158_pit_v1
  -> multifactor_core_v1
```

Do not weaken an AlphaPack contract to make an incompatible dataset run.

## 7. Model comparison

The built-in portable model presets are Ridge, LightGBM, XGBoost and optional PyTorch.

Run the default matrix:

```bash
bash scripts/run_local_research.sh matrix
```

Or keep the same AlphaPack while comparing models:

```bash
bash scripts/run_local_research.sh matrix \
  --alpha-pack alpha158_market_v1 \
  --model ridge --model lightgbm --model xgboost
```

Add PyTorch only when needed:

```bash
.venv/bin/python -m pip install -c constraints/ci.txt -e '.[dev,pytorch]'
bash scripts/run_local_research.sh run --alpha-pack alpha158_pit_v1 --model pytorch
```

PyTorch auto runtime resolves CUDA -> MPS -> CPU where supported.

## 8. Explicit train / valid / test windows

Provide all three together:

```bash
bash scripts/run_local_research.sh run \
  --alpha-pack alpha158_pit_v1 \
  --model lightgbm \
  --train 2018-10-01 2024-12-27 \
  --valid 2025-01-08 2025-07-02 \
  --test 2025-07-11 2026-08-10
```

Partial explicit split definitions are rejected. Do not move test boundaries after inspecting results and continue treating the same observations as unseen OOS data.

## 9. Walk-forward research

After narrowing the exploratory recipe:

```bash
bash scripts/run_local_research.sh run \
  --mode walk-forward \
  --alpha-pack alpha158_pit_v1 \
  --model lightgbm \
  --start 2019-01-01 \
  --end 2026-08-10
```

The wrapper delegates to the governed `research-run --mode walk-forward --stage release` implementation and preserves checkpoint isolation per AlphaPack/model pair.

## 10. Results and comparison

Quickstart orchestration output:

```text
data/output/quickstart/<timestamp>-run/
  configs/
  research_matrix.json
  research_matrix.md
```

Authoritative research runs remain under:

```text
data/output/research/<RUN_ID>/
```

Generate a comparison table:

```bash
.venv/bin/python -m qlib_platform.research.reporting.summary \
  data/output/quickstart/<RUN>/research_matrix.json
```

Supported evidence includes IC, RankIC, ICIR, RankICIR, ExcessIR, maximum drawdown, turnover when actually available, cost evidence, and manifest paths. Missing metrics remain missing rather than being fabricated.

## 11. Plan before expensive work

`plan` writes generated overlays and exact commands without training:

```bash
bash scripts/run_local_research.sh plan \
  --alpha-pack alpha158_pit_v1 \
  --model ridge --model lightgbm
```

`run` and `matrix` also support `--dry-run`. Dry-run intentionally does not auto-materialize a missing DatasetVersion.

## 12. Direct Qlib `qrun`

Use the maintained native example when the goal is Qlib YAML or a raw Qlib `Model` plugin:

```bash
.venv/bin/python examples/local_qlib_backtest/run_backtest.py --model lightgbm
.venv/bin/python examples/local_qlib_backtest/run_backtest.py --model ridge
.venv/bin/python examples/local_qlib_backtest/run_backtest.py --model custom_ridge
```

The native runner still verifies and binds an immutable DatasetVersion before qrun.

## Troubleshooting

### `unknown dataset reference: standalone-current`

Do not create an alias manually. Run the normal quickstart again on the fixed version:

```bash
bash scripts/run_local_research.sh run --alpha-pack alpha158_market_v1 --model lightgbm
```

The default run will recover an existing matching DatasetVersion or materialize one from the newest compatible frozen release.

### `DATA_UNAVAILABLE`

Either place usable local data/provider bytes under the configured `QLIB_DATA_ROOT`, set `QLIB_DATA_URI` to an existing provider, or add `TUSHARE_TOKEN` to `.env`.

### `DATA_INCOMPATIBLE`

A release exists but lacks the materialization component required by standalone research. Use a release profile containing `qlib_staging`, import an existing Qlib provider, or rebuild from capable local raw data. The resolver will not pretend a legacy release has fields it does not contain.

### Dataset verification fails

Do not bypass verification by pointing Qlib at a mutable directory. Repair/reimport the source or rebuild from governed inputs, then rerun the quickstart.

### Alpha158 Market works but Daily/PIT fails

The active DatasetVersion likely lacks the additional daily/PIT fields or release components. Use a capable DataRelease/DatasetVersion rather than weakening the pack contract.

### Explicit historical replay

Advanced users can still address archived immutable releases by exact ID. Integrated mode continues to require explicit selection when several releases are valid candidates.

## Recommended progression

```text
1. cp .env.example .env
2. run Alpha158 Market + LightGBM
3. catalog / doctor only when needed
4. compare Ridge / LightGBM / XGBoost
5. move to Daily/PIT only when the dataset contract supports it
6. compare IC/RankIC + portfolio economics
7. freeze the intended exploratory recipe
8. run walk-forward OOS
9. enter governed candidate/holdout workflows only when authorized
```

The key usability rule is: **normal standalone research is alias-driven and self-preparing; immutable IDs remain evidence, not configuration.**
