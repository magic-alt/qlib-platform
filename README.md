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
  <img alt="Artifact Contract" src="https://img.shields.io/badge/Artifact%20Contract-v2-2EA44F">
</p>

[Quick Start](#quick-start) · [Architecture](#architecture) · [Capabilities](#capabilities) · [Documentation](docs/index.md) · [CLI Reference](docs/cli_reference.md) · [Roadmap](docs/project/roadmap.md) · [Contributing](CONTRIBUTING.md)

</div>

`qlib-platform` is an A-share quantitative research platform built on [Microsoft Qlib](https://github.com/microsoft/qlib). It turns local market data or an existing Qlib provider into reproducible **Alpha158/custom-factor → machine-learning → OOS prediction → portfolio backtest → walk-forward** research with explicit DatasetVersion identity and auditable artifacts.

Use it standalone for local research. Integration with a downstream execution platform is optional and is not required for the workflows in this README.

---

## Why qlib-platform

A successful backtest is not enough for production-grade quantitative research. A research platform must also answer:

- **Which exact data was used?** Raw data, adjustment rules, calendars and PIT inputs need stable identity.
- **Can the experiment be reproduced?** Dataset, features, labels, splits, model profile and code revision must be pinned.
- **Can models be compared fairly?** Model or feature changes should not silently change the dataset, label, split or execution assumptions.
- **Does the signal survive out of sample?** IC, RankIC, portfolio economics and rolling OOS stability are first-class outputs.
- **Can the result be audited?** Predictions, simulated fills, strategy decisions and reports should be replayable and independently verifiable.

### What makes it different

| Principle | What it means in this repository |
| --- | --- |
| **Immutable by construction** | `DataRelease`, `DatasetVersion`, feature/prediction snapshots and research artifacts carry explicit identity and lineage. |
| **Alpha research first** | Alpha158 Market/Daily/PIT, multifactor packs and custom handlers are exposed as reproducible research inputs. |
| **Multi-model by design** | Ridge, LightGBM, XGBoost and optional PyTorch share the same DatasetVersion and research protocol. |
| **Walk-forward first** | Time-series research supports governed rolling OOS evaluation rather than relying on a single random split. |
| **Evidence over claims** | IC/RankIC, ICIR, portfolio metrics, stability, attribution and audit artifacts are persisted as evidence. |
| **Standalone by default** | Local research does not require another repository, QMT or a TuShare credential unless the selected data workflow needs them. |

---

## Architecture

<p align="center">
  <img src="docs/assets/architecture/system-overview.svg" alt="qlib-platform architecture overview" width="100%">
</p>

The diagram is an orientation view. Detailed ownership, data flow and identity rules live in [Architecture Overview](docs/architecture.md), [Architecture Boundary](docs/architecture_boundary.md) and [Identity and Lineage](docs/identity_and_lineage.md).

---

## Quick start

The fastest supported research path is the repository's `tq-research` quickstart. It is a thin orchestration layer over the existing DatasetVersion verifier, `train-select`, `backtest-predictions` and `research-run`; it does not create a second research engine.

### 1. Install the research environment

Requirements:

- Python `>=3.10,<3.13`; Python **3.12** is recommended.
- Windows, Linux or macOS.
- `pyqlib==0.9.7` is installed by the research extras below.

#### Linux / macOS

```bash
git clone https://github.com/magic-alt/qlib-platform.git
cd qlib-platform

python3.12 -m venv .venv
RepoPython=.venv/bin/python
$RepoPython -m pip install --upgrade pip
$RepoPython -m pip install -c constraints/ci.txt -e '.[all,dev]'
```

#### Windows PowerShell

```powershell
git clone https://github.com/magic-alt/qlib-platform.git
cd qlib-platform

python3.12 -m venv .venv
$RepoPython = '.\.venv\Scripts\python.exe'
& $RepoPython -m pip install --upgrade pip
& $RepoPython -m pip install -c constraints\ci.txt -e ".[all,dev]"
```

`all` installs the data helpers, Qlib, LightGBM and XGBoost. PyTorch is intentionally optional:

```bash
$RepoPython -m pip install -c constraints/ci.txt -e '.[dev,pytorch]'
```

The commands below use the maintained wrappers, which always invoke the repository-local `.venv` interpreter:

```text
Windows:       .\scripts\run_local_research.ps1 <command> ...
macOS/Linux:   bash scripts/run_local_research.sh <command> ...
```

The equivalent installed console command is `tq-research`, but it is only on `PATH` when the environment containing the editable/wheel install is active. If your shell is still in another environment (for example Conda `base`), use the wrapper above or activate `.venv` first:

```powershell
& .\.venv\Scripts\Activate.ps1
tq-research catalog
```

### 2. Check the local data first

If the repository already contains `data/`, or you already have a Qlib provider, start with `doctor` instead of rebuilding anything.

```powershell
# Windows
.\scripts\run_local_research.ps1 doctor
```

```bash
# macOS / Linux
bash scripts/run_local_research.sh doctor
```

`doctor` resolves the current data source, uses bounded deterministic sampled verification by default, checks AlphaPack compatibility and probes Ridge/LightGBM/XGBoost/PyTorch runtimes. Use `--verify-mode deep` only when you explicitly want a fresh/full diagnostic pass.

If data needs to be imported or materialized, let the source resolver choose the supported path:

```powershell
.\scripts\run_local_research.ps1 prepare --source auto
```

```bash
bash scripts/run_local_research.sh prepare --source auto
```

If an upgraded checkout reports `RELEASE_SELECTION_REQUIRED`, the repository has multiple immutable
DataReleases but no active release alias. This is intentionally fail-closed and is independent of the
operating system. Select the intended release explicitly, then rerun `prepare`:

```bash
.venv/bin/tq --config configs/pipeline.standalone.yaml release list
.venv/bin/tq --config configs/pipeline.standalone.yaml release promote <DATA_RELEASE_ID> --alias research-release-current
bash scripts/run_local_research.sh prepare --source auto
```

`prepare` now repairs `standalone-current` automatically when exactly one registered DatasetVersion is
bound to that explicitly selected DataRelease. If an older checkout left DatasetVersion manifests on
disk but the registry lost them, rebuild the registry first and retry:

```bash
.venv/bin/tq --config configs/pipeline.standalone.yaml registry-rebuild --root data
.venv/bin/tq --config configs/pipeline.standalone.yaml dataset-list --name cn_standalone
```

The tool never chooses between multiple historical DataReleases or multiple candidate DatasetVersions.
Those cases remain explicit operator decisions so research lineage cannot silently drift.

For an existing Qlib binary provider, import it explicitly:

```powershell
.\scripts\run_local_research.ps1 prepare --source qlib --path D:\quant\cn_data
```

```bash
bash scripts/run_local_research.sh prepare --source qlib --path /data/qlib/cn_data
```

The equivalent low-level command is:

```powershell
& $RepoPython -m qlib_platform release import-qlib --path <QLIB_PROVIDER>
```

The import freezes the provider into an immutable exploratory DataRelease/DatasetVersion. It does **not** invent missing daily-basic or PIT-fundamental fields.

Other supported preparation paths are:

```powershell
.\scripts\run_local_research.ps1 prepare --source raw --start 20160104 --end 20260810
.\scripts\run_local_research.ps1 prepare --source tushare --start 20160104 --end 20260810  # requires TUSHARE_TOKEN
```

### 3. Choose the AlphaPack for the data you actually have

```powershell
.\scripts\run_local_research.ps1 catalog
```

| AlphaPack | Intended data | Typical use |
| --- | --- | --- |
| `alpha158_market_v1` | OHLCV + basic market fields | safest Alpha158 baseline for an imported Qlib provider |
| `alpha158_daily_v1` | Market + daily liquidity/valuation/state fields | richer daily cross-sectional research |
| `alpha158_pit_v1` | Daily fields + PIT fundamentals | Alpha158 with point-in-time fundamentals |
| `multifactor_core_v1` | PIT fundamentals + PIT industry classification | momentum/value/quality/growth/size/liquidity factor research |

A practical escalation path is:

```text
alpha158_market_v1
    -> alpha158_daily_v1
    -> alpha158_pit_v1
    -> multifactor_core_v1
```

Start with `alpha158_market_v1` for an arbitrary imported Qlib dataset. Move to Daily/PIT packs only after `doctor` and the DatasetVersion contract show the required fields are available.

### 4. Run your first Alpha158 + LightGBM study

#### Windows

```powershell
.\scripts\run_local_research.ps1 run --alpha-pack alpha158_market_v1 --model lightgbm
```

#### macOS / Linux

```bash
bash scripts/run_local_research.sh run --alpha-pack alpha158_market_v1 --model lightgbm
```

`run` and `matrix` retain deep DatasetVersion verification. For an immutable DatasetVersion, a valid manifest-bound deep receipt or the collocated build-time full-hash proof is reused only while every partition remains unchanged since that proof; all paths/sizes are checked and a deterministic content sample is rehashed. Any stale or mutated payload invalidates reuse and falls back to a fresh full deep pass.

The default fixed-mode research path is:

```text
verified DatasetVersion
    -> Alpha158 Market features
    -> LightGBM fit on train/valid
    -> immutable OOS PredictionSnapshot
    -> signal metrics: IC / RankIC / ICIR / RankICIR
    -> prediction-only Qlib portfolio backtest
    -> research manifest + comparison-ready artifacts
```

The quickstart defaults to `train-select --stage signal`, so the first experiment measures signal quality without promoting a model. It then feeds the immutable OOS predictions into `backtest-predictions`; the portfolio simulation does not refit the model.

Outputs are written under:

```text
data/output/quickstart/<timestamp>-run/
  configs/
  research_matrix.json
  research_matrix.md
```

Each underlying run also writes its authoritative research artifacts under `data/output/research/<RUN_ID>/`.

### 5. Freeze explicit train / valid / test windows

For controlled model comparison, provide all three windows together. From a Windows checkout:

```powershell
.\scripts\run_local_research.ps1 run `
  --alpha-pack alpha158_pit_v1 `
  --model lightgbm `
  --train 2018-10-01 2024-12-27 `
  --valid 2025-01-08 2025-07-02 `
  --test 2025-07-11 2026-08-10
```

The wrapper rejects partial split definitions, and the underlying research runner validates chronological non-overlap. Do not move the test boundary after inspecting its performance and continue treating the same window as unseen OOS data.

### 6. Compare machine-learning models on the same Alpha158 data

A safe first matrix for a standard imported Qlib provider is:

```powershell
.\scripts\run_local_research.ps1 matrix `
  --alpha-pack alpha158_market_v1 `
  --model ridge --model lightgbm --model xgboost
```

When the DatasetVersion supports all Daily/PIT fields, the default matrix expands to:

```text
Alpha158 Market × Ridge / LightGBM / XGBoost
Alpha158 Daily  × Ridge / LightGBM / XGBoost
Alpha158 PIT    × Ridge / LightGBM / XGBoost
```

Run it with:

```powershell
.\scripts\run_local_research.ps1 matrix
```

Add PyTorch explicitly after installing the `pytorch` extra:

```powershell
.\scripts\run_local_research.ps1 matrix `
  --model ridge --model lightgbm --model xgboost --model pytorch
```

Run another feature family with the same model interface:

```powershell
.\scripts\run_local_research.ps1 run --alpha-pack multifactor_core_v1 --model lightgbm
```

Or pass a registered custom `ModelAdapter` profile:

```powershell
.\scripts\run_local_research.ps1 run `
  --alpha-pack alpha158_pit_v1 `
  --model-profile configs/model_profiles/my_adapter.yaml
```

Use `.\scripts\run_local_research.ps1 plan ...` or add `--dry-run` before an expensive matrix to inspect the exact generated AlphaPack overlays and commands without training.

### 7. Compare IC, RankIC and portfolio economics

After a completed run or matrix, use the repository-local interpreter so the summary command does not depend on shell `PATH`:

```powershell
& $RepoPython -m qlib_platform.research.reporting.summary `
  data\output\quickstart\<RUN>\research_matrix.json
```

Equivalent macOS/Linux invocation:

```bash
$RepoPython -m qlib_platform.research.reporting.summary \
  data/output/quickstart/<RUN>/research_matrix.json
```

The comparison report combines supported evidence including:

- IC and RankIC;
- ICIR and RankICIR;
- excess information ratio;
- maximum drawdown;
- turnover when exposed by the Qlib portfolio report;
- transaction-cost evidence;
- research and portfolio manifest paths.

It writes `research_comparison.json` and `research_comparison.md` next to the matrix. Missing metrics are left missing rather than replaced with invented proxies.

### 8. Run rolling walk-forward OOS research

After narrowing the exploratory recipe, move from a fixed split to rolling OOS:

```powershell
.\scripts\run_local_research.ps1 run `
  --mode walk-forward `
  --alpha-pack alpha158_market_v1 `
  --model lightgbm `
  --start 2019-01-01 `
  --end 2026-08-10
```

For a PIT-capable DatasetVersion, substitute `alpha158_pit_v1`. The quickstart delegates to the repository's existing `research-run --mode walk-forward --stage release` implementation and keeps a separate checkpoint namespace for every AlphaPack/model pair.

### 9. Use native Qlib `qrun` when that is the goal

If you want to learn Qlib workflow YAML, compare the maintained native workflows, or implement a raw Qlib `Model` plugin, use the dedicated example:

```bash
$RepoPython examples/local_qlib_backtest/run_backtest.py --model lightgbm
$RepoPython examples/local_qlib_backtest/run_backtest.py --model ridge
$RepoPython examples/local_qlib_backtest/run_backtest.py --model custom_ridge
```

The runner performs:

```text
dataset-resolve
    -> dataset-verify --mode deep
    -> bind immutable QLIB_DATA_URI
    -> validate-qrun-contract
    -> repository-local qrun
```

For the complete local-research command sheet, Feature Store usage, custom ModelAdapter guidance and troubleshooting, see **[Local Research Quickstart](docs/local_research_quickstart.md)**. For exact primitive syntax, see **[CLI Reference](docs/cli_reference.md)**.

### Optional environment configuration

Copy `.env.example` only when a workflow needs custom data roots or external data access:

```bash
cp .env.example .env
```

Common variables:

```text
QLIB_DATA_ROOT=/absolute/path/to/qlib-platform-data
TUSHARE_TOKEN=...              # optional; only for TuShare downloads
QUANT_DATA_ROOT=...            # optional; integrated mode
DATASET_RELEASE_ID=...         # optional; integrated mode
```

Never commit secrets or token values.

---

## Core workflow

```text
DataRelease
    -> materialize / import
DatasetVersion
    -> AlphaPack / FeatureSnapshot
    -> fixed OOS or walk-forward model research
    -> PredictionSnapshot
    -> IC / RankIC / stability evaluation
    -> research backtest + strategy audit
    -> portfolio / research artifacts
```

The central invariant is simple: **research must never silently change the identity of its inputs**.

Two identifiers that must not be confused:

- `release verify` verifies a **DataRelease**;
- `dataset-verify` verifies a **DatasetVersion** or dataset reference;
- research consumers using `--dataset-ref` expect a DatasetVersion ID or alias, not a DataRelease ID.

Changing data, features, labels, split rules, model profile, portfolio policy or implementation identity creates a new research identity rather than mutating existing evidence.

---

## Capabilities

| Area | Highlights |
| --- | --- |
| **Data release** | immutable release build/import/verify/promote, local or external inputs |
| **Dataset lifecycle** | materialization, registry, aliases, verification and migration |
| **Feature engineering** | Qlib handlers, Alpha158 Market/Daily/PIT, custom features, feature snapshots, PIT fundamentals |
| **Model research** | Ridge, LightGBM, XGBoost, optional PyTorch and custom ModelAdapter profiles |
| **Research protocol** | fixed OOS studies, prediction-only portfolio backtests and rolling walk-forward research |
| **Evaluation** | IC, RankIC, ICIR, stability, regime, attribution, explanation and prediction feedback |
| **Backtesting** | Qlib research backtest, simulated fills, audit and reporting |
| **Portfolio construction** | `topk_dropout_v1`, `rank_buffer_v1`, target portfolio generation |
| **Artifact lineage** | immutable research manifests, PredictionSnapshots, validation and portable evidence |
| **Operations** | health checks, runtime probes, recovery and observability |

### Installation profiles

| Extra | Purpose |
| --- | --- |
| `data` | TuShare / MySQL-oriented ingestion helpers |
| `qlib` | `pyqlib==0.9.7` + LightGBM |
| `xgboost` | XGBoost research |
| `pytorch` | PyTorch research |
| `all` | data + Qlib + LightGBM + XGBoost |
| `dev` | tests, lint, typing and research dependencies |
| `docs` | MkDocs Material documentation-site tooling |

Examples:

```bash
# Full local research environment
$RepoPython -m pip install -c constraints/ci.txt -e '.[all,dev]'

# Add PyTorch when needed
$RepoPython -m pip install -c constraints/ci.txt -e '.[dev,pytorch]'

# Build the documentation site
$RepoPython -m pip install -e '.[docs]'
$RepoPython -m mkdocs build --strict
```

See [Configuration](docs/configuration.md) for canonical dependency and profile rules.

---

## Deployment modes

| Profile | Use case | External platform | TuShare |
| --- | --- | ---: | ---: |
| `configs/pipeline.standalone.yaml` | autonomous local research; **default** | No | only for downloads |
| `configs/pipeline.integrated.yaml` | consume an external immutable DataRelease | Yes | No |
| `configs/pipeline.yaml` | integrated canonical/base config | Yes | No |
| `configs/pipeline_candidate_research.yaml` | frozen governed Phase 2/3 profile | depends on release | No |
| `configs/pipeline_tushare_dev.yaml` | TuShare development | No | Yes |
| `configs/pipeline_lean_mysql.yaml` | legacy migration compatibility | legacy source | No |

Do not treat `pipeline.yaml` as a universal default. Select integrated mode explicitly when the workflow consumes an external DataRelease.

---

## Documentation

The canonical entry point is the **[Documentation Index](docs/index.md)**.

| Start here | Use it for |
| --- | --- |
| [Local Research Quickstart](docs/local_research_quickstart.md) | local data → Alpha158 → models → backtest → walk-forward |
| [CLI Reference](docs/cli_reference.md) | commands, side effects and key parameters |
| [Architecture](docs/architecture.md) | system layers, data flow, deployment modes and failure model |
| [Identity and Lineage](docs/identity_and_lineage.md) | immutable identities and parent/child relationships |
| [Configuration](docs/configuration.md) | profiles, environment variables and dependency extras |
| [Current State](docs/current_state.md) | active research-program state and authorization facts |
| [Research Lifecycle](docs/research_lifecycle.md) | governed research stages |
| [Operations Runbook](docs/OPERATIONS_RUNBOOK.md) | operational procedures and recovery entry points |
| [Testing and Certification](docs/testing_and_certification.md) | validation and certification model |
| [Troubleshooting](docs/troubleshooting.md) | common failures and recovery guidance |
| [Roadmap](docs/project/roadmap.md) | public engineering direction and milestone criteria |
| [Release Process](docs/maintainers/releasing.md) | software versioning, release notes and rollback |
| [Repository Governance](docs/maintainers/repository-governance.md) | Ruleset, dependency, security and supply-chain policy |

Historical protocols and deprecated tutorials live under [`docs/history`](docs/history/README.md) and must not be treated as current operating instructions.

---

## Development

Before opening a pull request, run the repository checks with the repository interpreter:

```bash
$RepoPython scripts/check_docs.py --root .
$RepoPython -m ruff check src tests
$RepoPython -m ruff format --check src tests
$RepoPython -m mypy src
$RepoPython -m qlib_platform --config configs/pipeline.integrated.yaml validate-qrun-contract
$RepoPython -m pytest
```

Coverage:

```bash
$RepoPython -m pytest --cov=src/qlib_platform --cov-report=term-missing
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for contributor onboarding, change classification, validation expectations and pull-request guidance.

---

## Relationship to Microsoft Qlib

`qlib-platform` **uses and extends Qlib; it is not a fork or replacement of Qlib**.

Microsoft Qlib provides the underlying quantitative ML framework, dataset interfaces, models, strategies, records and backtest machinery. This repository adds an A-share research-engineering layer around it: immutable data releases, DatasetVersion identity, PIT-aware research inputs, reusable AlphaPacks, multi-model runtime profiles, fixed/walk-forward OOS workflows, independent auditing and reproducible artifacts.

Upstream: [microsoft/qlib](https://github.com/microsoft/qlib)

---

## Contributing & community

Contributions that improve reproducibility, data integrity, research tooling, documentation, testing or operational safety are welcome. New contributors should start with [CONTRIBUTING.md](CONTRIBUTING.md) and the [Good First Issue guide](docs/project/good-first-issues.md).

Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md). For suspected vulnerabilities, credential exposure, artifact-integrity bypasses or security-boundary issues, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

Software release history is tracked in [CHANGELOG.md](CHANGELOG.md); maintainer release procedure lives in [docs/maintainers/releasing.md](docs/maintainers/releasing.md).

---

## License

Licensed under the [Apache License 2.0](LICENSE). Third-party projects, datasets and dependencies retain their own licenses and attribution requirements.

---

## Disclaimer

This repository is research and engineering software. Backtests, model scores, diagnostics, target portfolios and generated artifacts are **not investment advice** and do not guarantee future performance.
