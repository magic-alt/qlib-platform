<div align="center">

<img src="docs/assets/brand/qlib-platform-logo.svg" alt="qlib-platform" width="760">

### Auditable A-share Quant Research & Alpha Factory on Microsoft Qlib

**Immutable data lineage · Alpha158 & custom factors · Multi-model research · Walk-forward evaluation · Reproducible Qlib workflows**

<p>
  <a href="https://github.com/magic-alt/qlib-platform/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/magic-alt/qlib-platform/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/magic-alt/qlib-platform/actions/workflows/docs.yml"><img alt="Docs" src="https://github.com/magic-alt/qlib-platform/actions/workflows/docs.yml/badge.svg"></a>
  <a href="https://github.com/magic-alt/qlib-platform/actions/workflows/codeql.yml"><img alt="CodeQL" src="https://github.com/magic-alt/qlib-platform/actions/workflows/codeql.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/License-Apache--2.0-D22128.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10--3.12-3776AB?logo=python&logoColor=white">
  <img alt="Qlib" src="https://img.shields.io/badge/Microsoft%20Qlib-0.9.7-5C2D91">
  <img alt="Market" src="https://img.shields.io/badge/Market-A--share-C62828">
</p>

[Quick Start](#quick-start) · [Architecture](#architecture) · [Capabilities](#capabilities) · [Documentation](docs/index.md) · [Roadmap](docs/project/roadmap.md) · [Contributing](CONTRIBUTING.md)

</div>

`qlib-platform` is an A-share quantitative research platform built on [Microsoft Qlib](https://github.com/microsoft/qlib). It turns local market data, an existing Qlib provider, or governed data releases into reproducible **Alpha158/custom-factor → machine-learning → OOS prediction → portfolio backtest → walk-forward** research.

The default experience is standalone and local. Immutable `DataRelease` and `DatasetVersion` identities remain part of the evidence model, but normal users do **not** configure content hashes or registry aliases by hand.

---

## Why qlib-platform

A successful backtest is not enough for production-grade quantitative research. The platform also answers:

- **Which exact data was used?** Raw inputs, adjustment rules, PIT data and calendars carry stable identity.
- **Can the experiment be reproduced?** Dataset, features, labels, splits, model profile and code revision are pinned.
- **Can models be compared fairly?** Model changes do not silently change data or execution assumptions.
- **Does the signal survive out of sample?** IC, RankIC, portfolio economics and rolling OOS stability are first-class outputs.
- **Can the result be audited?** Predictions, simulated fills, strategy decisions and reports remain replayable evidence.

| Principle | Repository behavior |
| --- | --- |
| **Immutable by construction** | DataRelease, DatasetVersion, feature/prediction snapshots and research artifacts carry identity and lineage. |
| **Alpha research first** | Alpha158 Market/Daily/PIT, multifactor packs and custom handlers are reusable research inputs. |
| **Multi-model by design** | Ridge, LightGBM, XGBoost and optional PyTorch share the same data/research protocol. |
| **Walk-forward first** | Time-series research supports governed rolling OOS evaluation. |
| **Standalone by default** | No downstream execution platform is required for normal local research. |
| **Zero-config lifecycle** | Standalone quickstart manages release selection, DatasetVersion materialization and aliases automatically. |

---

## Architecture

<p align="center">
  <img src="docs/assets/architecture/system-overview.svg" alt="qlib-platform architecture overview" width="100%">
</p>

```text
Local data / Qlib provider / DataRelease
                 |
                 v
         immutable DatasetVersion
                 |
                 v
       AlphaPack / FeatureSnapshot
                 |
       +---------+---------+
       |         |         |
     Ridge    LightGBM   XGBoost   (+ PyTorch)
       |         |         |
       +---------+---------+
                 |
                 v
          OOS PredictionSnapshot
                 |
        IC / RankIC / stability
                 |
                 v
       Qlib portfolio backtest
                 |
                 v
       research + audit artifacts
```

Detailed ownership and identity rules live in [Architecture](docs/architecture.md), [Architecture Boundary](docs/architecture_boundary.md) and [Identity and Lineage](docs/identity_and_lineage.md).

---

## Quick start

The normal standalone contract is:

> **Copy `.env.example`, then run research. YAML, `ds_*`, DatasetVersion IDs, alias promotion and registry repair are not onboarding steps.**

### 1. Install

Python `>=3.10,<3.13`; Python 3.12 is recommended.

#### macOS / Linux

```bash
git clone https://github.com/magic-alt/qlib-platform.git
cd qlib-platform
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -c constraints/ci.txt -e '.[all,dev]'
cp .env.example .env
```

#### Windows PowerShell

```powershell
git clone https://github.com/magic-alt/qlib-platform.git
cd qlib-platform
python3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -c constraints\ci.txt -e ".[all,dev]"
Copy-Item .env.example .env
```

The copied `.env` is valid as-is:

```dotenv
QLIB_DATA_ROOT=./data
TUSHARE_CALLS_PER_MINUTE=180
TUSHARE_TOKEN=
QLIB_REPO=
QLIB_DATA_URI=
```

Fill only what this machine actually needs. `TUSHARE_TOKEN` is required only for TuShare downloads/refreshes; the other paths are optional overrides.

### 2. Run Alpha158 + LightGBM

#### macOS / Linux

```bash
bash scripts/run_local_research.sh run \
  --alpha-pack alpha158_market_v1 \
  --model lightgbm
```

#### Windows

```powershell
.\scripts\run_local_research.ps1 run --alpha-pack alpha158_market_v1 --model lightgbm
```

On the default `standalone-current` reference, `run` self-prepares before training:

```text
resolve current DatasetVersion
  -> if missing, resolve local source
  -> import existing Qlib provider OR
     materialize newest compatible frozen DataRelease OR
     build from capable local raw data OR
     download through configured provider
  -> verify DatasetVersion
  -> run research
```

There is no required `release list`, `release promote`, manual `dataset-build`, or registry-rebuild step in the normal path.

### 3. Multiple `ds_*` releases are handled automatically

Standalone mode keeps one **active** release by default. When several historical local releases are present, quickstart selects the newest release that can actually materialize a Qlib research dataset, verifies/materializes it, promotes the matching release + DatasetVersion snapshot, and moves older immutable releases to:

```text
data/releases/archive/
```

Archived releases are not destroyed; exact immutable IDs remain usable for audit/replay. Integrated/advanced mode retains explicit fail-closed release selection.

This keeps content-addressed lineage internally without turning hashes into user configuration.

### 4. Diagnostics are optional

Use `doctor` when something is wrong, not as a mandatory preflight:

```bash
bash scripts/run_local_research.sh doctor
```

Optional explicit preparation:

```bash
bash scripts/run_local_research.sh prepare --source auto
```

If the default DatasetVersion is missing, the normal `run` command performs the same safe preparation automatically.

### 5. Choose AlphaPack and model

```bash
bash scripts/run_local_research.sh catalog
```

| AlphaPack | Intended data | Typical use |
| --- | --- | --- |
| `alpha158_market_v1` | OHLCV/basic market fields | safest Alpha158 baseline |
| `alpha158_daily_v1` | market + daily liquidity/valuation/state | richer daily cross-sectional research |
| `alpha158_pit_v1` | daily + PIT fundamentals | PIT-aware Alpha158 |
| `multifactor_core_v1` | PIT fundamentals + PIT industry | multifactor research |

Recommended escalation:

```text
alpha158_market_v1
  -> alpha158_daily_v1
  -> alpha158_pit_v1
  -> multifactor_core_v1
```

Compare models on the same pack:

```bash
bash scripts/run_local_research.sh matrix \
  --alpha-pack alpha158_market_v1 \
  --model ridge --model lightgbm --model xgboost
```

Run the default Alpha158 matrix:

```bash
bash scripts/run_local_research.sh matrix
```

### 6. Walk-forward OOS

After narrowing the exploratory recipe:

```bash
bash scripts/run_local_research.sh run \
  --mode walk-forward \
  --alpha-pack alpha158_pit_v1 \
  --model lightgbm \
  --start 2019-01-01 \
  --end 2026-08-10
```

### 7. Compare evidence

Quickstart writes orchestration evidence under:

```text
data/output/quickstart/<timestamp>-run/
  configs/
  research_matrix.json
  research_matrix.md
```

Underlying authoritative runs remain under `data/output/research/<RUN_ID>/`.

Generate a comparison report:

```bash
.venv/bin/python -m qlib_platform.research.reporting.summary \
  data/output/quickstart/<RUN>/research_matrix.json
```

The report combines supported IC, RankIC, ICIR, RankICIR, ExcessIR, maximum drawdown, turnover when available, transaction-cost evidence and manifest paths. Missing metrics stay missing rather than being replaced by proxies.

For the complete command sheet and advanced lifecycle behavior, see [Local Research Quickstart](docs/local_research_quickstart.md).

---

## Core workflow and identity

```text
DataRelease
    -> exact materialization / import
DatasetVersion
    -> AlphaPack / FeatureSnapshot
    -> fixed OOS or walk-forward model research
    -> PredictionSnapshot
    -> IC / RankIC / stability evaluation
    -> research backtest + strategy audit
    -> portfolio / research artifacts
```

The central invariant is: **research must never silently change the identity of its inputs**.

Standalone aliases are stable selectors:

```text
research-release-current   -> active immutable DataRelease
standalone-current         -> active immutable Qlib DatasetVersion
```

They are lifecycle state, not `.env` configuration. The quickstart advances them only after successful verification/materialization.

Advanced low-level commands still exist for maintenance and integrated pipelines:

```text
release list / verify / promote
dataset-list / dataset-resolve / dataset-verify / dataset-promote
registry-rebuild
dataset-build
```

Normal standalone users should not need them.

---

## Capabilities

| Area | Highlights |
| --- | --- |
| **Data lifecycle** | immutable release build/import/verify, DatasetVersion materialization, registry and aliases |
| **Feature engineering** | Alpha158 Market/Daily/PIT, custom handlers, feature snapshots, PIT fundamentals |
| **Model research** | Ridge, LightGBM, XGBoost, optional PyTorch and custom ModelAdapter profiles |
| **Research protocol** | fixed OOS studies, prediction-only portfolio backtests, rolling walk-forward research |
| **Evaluation** | IC, RankIC, ICIR, stability, regime, attribution, explanation and prediction feedback |
| **Backtesting** | Qlib research backtest, simulated fills, strategy audit and reports |
| **Portfolio construction** | TopK dropout, rank buffer and target-portfolio generation |
| **Artifact lineage** | immutable manifests, PredictionSnapshots, validation results and portable evidence |
| **Operations** | health checks, runtime probes, recovery and observability |

### Installation profiles

| Extra | Purpose |
| --- | --- |
| `data` | TuShare / MySQL-oriented ingestion helpers |
| `qlib` | `pyqlib==0.9.7` + LightGBM |
| `xgboost` | XGBoost research |
| `pytorch` | PyTorch research |
| `all` | data + Qlib + LightGBM + XGBoost |
| `dev` | tests, lint and typing |
| `docs` | MkDocs Material tooling |

---

## Deployment modes

| Profile | Use case | External platform | TuShare |
| --- | --- | ---: | ---: |
| `configs/pipeline.standalone.yaml` | autonomous local research; **default** | No | only for downloads |
| `configs/pipeline.integrated.yaml` | consume an external immutable DataRelease | Yes | No |
| `configs/pipeline_candidate_research.yaml` | governed candidate research | depends on release | No |
| `configs/pipeline_tushare_dev.yaml` | TuShare development | No | Yes |
| `configs/pipeline_lean_mysql.yaml` | migration compatibility | legacy source | No |

`pipeline.standalone.yaml` is the normal local profile. Other YAML profiles are advanced overrides, not required onboarding configuration.

---

## Documentation

| Start here | Use it for |
| --- | --- |
| [Local Research Quickstart](docs/local_research_quickstart.md) | zero-config local data → Alpha158 → model → backtest → walk-forward |
| [CLI Reference](docs/cli_reference.md) | exact primitive syntax and side effects |
| [Architecture](docs/architecture.md) | system layers and failure model |
| [Identity and Lineage](docs/identity_and_lineage.md) | immutable identities and parent/child relationships |
| [Configuration](docs/configuration.md) | optional profiles, environment variables and extras |
| [Current State](docs/current_state.md) | active governed research state |
| [Research Lifecycle](docs/research_lifecycle.md) | governed research stages |
| [Operations Runbook](docs/OPERATIONS_RUNBOOK.md) | recovery and operations |
| [Testing and Certification](docs/testing_and_certification.md) | validation/certification model |
| [Troubleshooting](docs/troubleshooting.md) | common failures |
| [Roadmap](docs/project/roadmap.md) | engineering direction |

Historical protocols under [`docs/history`](docs/history/README.md) are not current operating instructions.

---

## Development

Before opening a pull request:

```bash
.venv/bin/python scripts/check_docs.py --root .
.venv/bin/python -m ruff check src tests
.venv/bin/python -m ruff format --check src tests
.venv/bin/python -m mypy src
.venv/bin/python -m pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for contributor onboarding and validation expectations.

---

## Relationship to Microsoft Qlib

`qlib-platform` **uses and extends Qlib; it is not a fork or replacement of Qlib**.

Microsoft Qlib provides the quantitative ML framework, dataset interfaces, models, strategies, records and backtest machinery. This repository adds the A-share research-engineering layer: immutable releases, DatasetVersion identity, PIT-aware inputs, reusable AlphaPacks, runtime profiles, fixed/walk-forward OOS workflows and auditable artifacts.

Upstream: [microsoft/qlib](https://github.com/microsoft/qlib)

---

## Contributing & community

Contributions improving reproducibility, data integrity, research tooling, documentation, testing or operational safety are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md) and [Good First Issues](docs/project/good-first-issues.md).

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Security reports should follow [SECURITY.md](SECURITY.md).

---

## License

Licensed under the [Apache License 2.0](LICENSE).

## Disclaimer

This repository is research and engineering software. Backtests, model scores, diagnostics, target portfolios and generated artifacts are **not investment advice** and do not guarantee future performance.
