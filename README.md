<div align="center">

# qlib-platform

### Auditable A-share Quant Research & Alpha Factory on Microsoft Qlib

**Immutable data lineage · PIT-aware research · Walk-forward evaluation · Reproducible Qlib workflows · Governed portfolio handoff**

<p>
  <a href="https://github.com/magic-alt/qlib-platform/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/magic-alt/qlib-platform/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10--3.12-3776AB?logo=python&logoColor=white">
  <img alt="Qlib" src="https://img.shields.io/badge/Microsoft%20Qlib-0.9.7-5C2D91">
  <img alt="Market" src="https://img.shields.io/badge/Market-A--share-C62828">
  <img alt="Artifact Contract" src="https://img.shields.io/badge/Artifact%20Contract-v2-2EA44F">
</p>

[Quick Start](#quick-start) · [Architecture](#architecture) · [Documentation](docs/index.md) · [CLI Reference](docs/cli_reference.md) · [Current Governance State](docs/current_state.md)

</div>

> [!IMPORTANT]
> `qlib-platform` is a **Research Plane**, not a broker execution engine. It does not submit, cancel, or replace real broker orders and does not own broker state. The only cross-repository execution handoff is a `TARGET_PORTFOLIO` bound to an immutable `DataRelease`.

`qlib-platform` 是一个面向 A 股量化研究的 **Research Plane / Alpha Factory**。项目以 [Microsoft Qlib](https://github.com/microsoft/qlib) 为核心研究引擎，把数据发布、DatasetVersion 物化、PIT 特征、模型研究、walk-forward、组合构建、研究审计和 artifact 交付组织成一条**可追踪、可复现、可验证**的研究流水线。

It can run as a standalone research platform or hand governed research artifacts to [`magic-alt/platform`](https://github.com/magic-alt/platform), the optional **Execution Plane** responsible for authoritative execution semantics, hard risk, OMS, QMT/broker integration, orders, fills, positions and ledger state.

---

## Why qlib-platform

A successful backtest is not enough for production-grade quantitative research. A research platform must also answer:

- **Which exact data release was used?** Raw data, adjustment rules, calendars and PIT inputs must have stable identity.
- **Can the experiment be reproduced?** Dataset, features, labels, splits, model profile, code revision and portfolio policy must be pinned.
- **Can a result be audited independently?** Research evidence, simulated fills and strategy decisions should be replayable and verifiable.
- **Was the holdout really sealed?** Diagnostics, candidate generation, model selection and holdout access must be governed as different lifecycle stages.
- **Where does research end and execution begin?** Target portfolios can cross the boundary; live broker state cannot.

`qlib-platform` therefore focuses on **quant research engineering**, not merely another wrapper around `qrun`.

### What makes it different

| Principle | What it means in this repository |
| --- | --- |
| **Immutable by construction** | `DataRelease`, `DatasetVersion`, feature/prediction snapshots and research artifacts carry explicit identity and lineage. |
| **Walk-forward first** | Time-series research is organized around governed OOS evaluation rather than a single random train/test split. |
| **Policy is explicit** | Portfolio construction is a typed research stage (`topk_dropout_v1`, `rank_buffer_v1`, etc.), not hidden ad-hoc logic. |
| **Evidence over claims** | IC/RankIC, stability, regime, attribution, backtest audit and validation artifacts are first-class outputs. |
| **Research/execution separation** | Research may publish a target portfolio; broker orders, fills, positions and ledger remain outside this repository. |
| **Standalone by default** | Local research does not require `platform`, QMT or a TuShare credential unless the selected workflow actually needs them. |

---

## Architecture

```mermaid
flowchart LR
    DR[Immutable DataRelease] --> DV[DatasetVersion]
    DV --> FS[FeatureSnapshot]
    FS --> RR[Research / Walk-forward]
    RR --> PS[PredictionSnapshot]
    PS --> BT[Research Backtest]
    PS --> PP[PortfolioPolicy]
    BT --> EV[Research Evidence / Audit]
    PP --> TP[TARGET_PORTFOLIO]
    TP --> AC[Artifact Contract v2]
    AC --> PX[platform / Execution Plane]

    DR --> RL[RealizedLabelSnapshot]
    PS --> PE[PredictionEvaluationSnapshot]
    RL --> PE
    PE --> MON[Monitoring Evidence]
```

### Research Plane — this repository

- DataRelease build / import / verify / promote
- immutable Qlib DatasetVersion materialization and registry
- PIT-aware data, Alpha158/custom handlers and feature snapshots
- LightGBM, XGBoost and optional PyTorch model research
- walk-forward, IC, RankIC, stability, regime and attribution analysis
- Qlib research backtests and strategy audit
- `PortfolioPolicy` and `TARGET_PORTFOLIO` construction
- `MODEL_RELEASE`, `SIGNAL_SNAPSHOT`, `VALIDATION_RESULT` and related research artifacts
- Artifact Contract v2 export, durable outbox and acknowledgement tracking
- research promotion up to `RESEARCH_PROMOTED`

### Execution Plane — `magic-alt/platform`

- authoritative LEAN backtest / execution semantics
- hard risk and execution controls
- paper / shadow / production trading
- OMS and QMT / broker gateway
- orders, fills, positions and ledger
- `LEAN_VALIDATED`, `PAPER`, `PRODUCTION`, `RETIRED`

See [Architecture Overview](docs/architecture.md), [Architecture Boundary](docs/architecture_boundary.md) and [Identity and Lineage](docs/identity_and_lineage.md).

---

## Quick start

### Requirements

- Python `>=3.10,<3.13`
- recommended local interpreter: **Python 3.12**
- `pyqlib==0.9.7` when using the Qlib extra
- Windows, Linux or macOS

### Linux / macOS

```bash
git clone https://github.com/magic-alt/qlib-platform.git
cd qlib-platform

python3.12 -m venv .venv
RepoPython=.venv/bin/python
$RepoPython -m pip install --upgrade pip
$RepoPython -m pip install -c constraints/ci.txt -e '.[dev]'

$RepoPython -m tushare_qlib status
$RepoPython -m tushare_qlib health dependencies
$RepoPython -m tushare_qlib release list
```

### Windows PowerShell

```powershell
git clone https://github.com/magic-alt/qlib-platform.git
cd qlib-platform

python3.12 -m venv .venv
$RepoPython = '.\.venv\Scripts\python.exe'
& $RepoPython -m pip install --upgrade pip
& $RepoPython -m pip install -c constraints/ci.txt -e ".[dev]"

& $RepoPython -m tushare_qlib status
& $RepoPython -m tushare_qlib health dependencies
& $RepoPython -m tushare_qlib release list
```

The CLI defaults to `configs/pipeline.standalone.yaml`. The standalone profile intentionally avoids external startup dependencies.

> [!TIP]
> New to the repository? Continue with the maintained [local Qlib backtest example](examples/local_qlib_backtest/README.md), then read the [Documentation Index](docs/index.md).

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
    -> materialize
DatasetVersion
    -> FeatureSnapshot
    -> research / walk-forward
    -> PredictionSnapshot / MODEL_RELEASE
    -> research backtest + audit
    -> PortfolioPolicy
    -> TARGET_PORTFOLIO
    -> Artifact Contract v2
    -> optional platform handoff
```

The central invariant is simple: **research must never silently change the identity of its inputs**.

Two identifiers that must not be confused:

- `release verify` verifies a **DataRelease**;
- `dataset-verify` verifies a **DatasetVersion** or dataset reference;
- `live-inference --dataset-ref` accepts a DatasetVersion ID or alias, not a DataRelease ID.

Changing data, features, labels, split rules, model profile, portfolio policy or implementation identity creates a new research identity rather than mutating existing evidence.

---

## Capabilities

| Area | Highlights |
| --- | --- |
| **Data release** | immutable release build/import/verify/promote, local or external inputs |
| **Dataset lifecycle** | materialization, registry, aliases, verification and migration |
| **Feature engineering** | Qlib handlers, Alpha158, custom features, feature snapshots, PIT fundamentals |
| **Model research** | LightGBM, XGBoost, optional PyTorch, explicit model profiles |
| **Research protocol** | fixed and walk-forward OOS studies, governed diagnostics and research runs |
| **Evaluation** | IC, RankIC, stability, regime, attribution, explanation and prediction feedback |
| **Backtesting** | Qlib research backtest, simulated fills, audit and reporting |
| **Portfolio construction** | `topk_dropout_v1`, `rank_buffer_v1`, target portfolio generation |
| **Artifact governance** | Artifact Contract v2, validation, promotion and portable evidence |
| **Operations** | health checks, runtime probes, outbox, recovery and observability |
| **Execution handoff** | DataRelease-bound `TARGET_PORTFOLIO` to optional `magic-alt/platform` |

### Installation profiles

| Extra | Purpose |
| --- | --- |
| `data` | TuShare / MySQL-oriented ingestion helpers |
| `qlib` | `pyqlib==0.9.7` + LightGBM |
| `xgboost` | XGBoost research |
| `pytorch` | PyTorch research |
| `all` | data + Qlib + LightGBM + XGBoost |
| `dev` | tests, lint, typing and research dependencies |

Examples:

```bash
# Full research environment
$RepoPython -m pip install -e '.[all,dev]'

# Add PyTorch to the governed development environment
$RepoPython -m pip install -c constraints/ci.txt -e '.[dev,pytorch]'
```

See [Configuration](docs/configuration.md) for canonical dependency and profile rules.

---

## Deployment modes

| Profile | Use case | External platform | TuShare |
| --- | --- | ---: | ---: |
| `configs/pipeline.standalone.yaml` | autonomous local research; **default** | No | only for downloads |
| `configs/pipeline.integrated.yaml` | consume an external immutable DataRelease | Yes | No |
| `configs/pipeline.yaml` | integrated canonical/base config | Yes | No |
| `configs/pipeline_phase2.yaml` | frozen governed Phase 2/3 profile | depends on release | No |
| `configs/pipeline_tushare_dev.yaml` | TuShare development | No | Yes |
| `configs/pipeline_lean_mysql.yaml` | legacy migration compatibility | legacy source | No |

Do not treat `pipeline.yaml` as a universal default. Select integrated mode explicitly when the workflow consumes an external DataRelease.

---

## CLI overview

Invoke the CLI with the repository interpreter:

```text
<repo-python> -m tushare_qlib [--config PROFILE] COMMAND
```

A `tq` console entry point is also installed, but the explicit interpreter is preferred for governed operations because it avoids accidentally invoking a global executable.

### Validation-first commands

```text
status
health live
health ready
health dependencies
runtime-probe
release list
release verify
dataset-list
dataset-show
dataset-resolve
dataset-verify
model-status
ops-query
ops-summary
validate-qrun-contract
project-audit
research-audit
```

### Research artifact commands

```text
feature-store
train-select
research-run
backtest-predictions
research-report
alpha-diagnose
regime-diagnose
attribution-diagnose
explanation-diagnose
build-target-portfolio
research-gate
artifact-v2-export
```

Some research and validation commands create immutable evidence. Treat an explicitly named output path as part of the authorized operation. The complete side-effect classification lives in the [CLI Reference](docs/cli_reference.md) and [Operations Runbook](docs/OPERATIONS_RUNBOOK.md).

---

## Research governance

Formal research separates diagnostics, candidate creation, model selection, final holdout access and publishing into distinct lifecycle stages. The active authorization state is intentionally **not duplicated in this README** because it changes over time.

**Before governed research, read → [Current Governance State](docs/current_state.md)**

That page is the source of truth for reviewed/certified baselines, active research phase, holdout state, publishing authorization and the current documentation-audit base.

> [!CAUTION]
> Generic CLI availability does not override active governance restrictions. A command existing in the parser does not mean the current research program authorizes it.

---

## Project layout

```text
qlib-platform/
├─ configs/                 # runtime, research, model and portfolio profiles
├─ constraints/             # CI/dev dependency constraints
├─ contracts/               # Artifact Contract schemas
├─ deploy/                  # systemd / launchd deployment assets
├─ docs/                    # architecture, lifecycle, operations and research docs
├─ examples/                # maintained runnable examples
├─ scripts/                 # validation and utility scripts
├─ src/tushare_qlib/        # application and research implementation
├─ tests/                   # unit / integration / contract tests
├─ .github/                 # CI and community workflow metadata
├─ .env.example             # environment-variable template
├─ AGENTS.md                # guidance for coding agents
├─ CONTRIBUTING.md          # contributor workflow and validation rules
├─ SECURITY.md              # vulnerability reporting policy
├─ pyproject.toml           # package metadata and dependency profiles
└─ README.md
```

---

## Documentation

The canonical entry point is the **[Documentation Index](docs/index.md)**.

| Start here | Use it for |
| --- | --- |
| [Current State](docs/current_state.md) | active governance facts and authorization state |
| [Architecture](docs/architecture.md) | system layers, data flow, deployment modes and failure model |
| [Architecture Boundary](docs/architecture_boundary.md) | Research Plane / Execution Plane ownership |
| [Identity and Lineage](docs/identity_and_lineage.md) | immutable identities and parent/child relationships |
| [Configuration](docs/configuration.md) | profiles, environment variables and dependency extras |
| [CLI Reference](docs/cli_reference.md) | commands, side effects and key parameters |
| [Research Lifecycle](docs/research_lifecycle.md) | governed research stages |
| [Operations Runbook](docs/OPERATIONS_RUNBOOK.md) | operational procedures and recovery entry points |
| [Testing and Certification](docs/testing_and_certification.md) | validation and certification model |
| [Troubleshooting](docs/troubleshooting.md) | common failures and recovery guidance |

Historical protocols and deprecated tutorials live under [`docs/history`](docs/history/README.md) and must not be treated as current operating instructions.

---

## Development

Before opening a pull request, run the repository checks with the repository interpreter:

```bash
$RepoPython scripts/check_docs.py --root .
$RepoPython -m ruff check src tests
$RepoPython -m ruff format --check src tests
$RepoPython -m mypy src
$RepoPython -m tushare_qlib --config configs/pipeline.integrated.yaml validate-qrun-contract
$RepoPython -m pytest
```

Coverage:

```bash
$RepoPython -m pytest --cov=src/tushare_qlib --cov-report=term-missing
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for change classification, documentation rules, validation expectations and pull-request guidance.

---

## Relationship to Microsoft Qlib

`qlib-platform` **uses and extends Qlib; it is not a fork or replacement of Qlib**.

Microsoft Qlib provides the underlying quantitative ML framework, dataset interfaces, models, strategies, records and backtest machinery. This repository adds an A-share research-engineering layer around it: immutable data releases, dataset identity, PIT-aware research inputs, governed research lifecycle, explicit portfolio policy, independent auditing, artifact contracts and a hard research/execution boundary.

Upstream: [microsoft/qlib](https://github.com/microsoft/qlib)

---

## Contributing & security

Contributions that improve reproducibility, data integrity, research tooling, documentation, testing or operational safety are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md) and use the repository issue / pull-request templates.

For suspected vulnerabilities, credential exposure, artifact-integrity bypasses or security-boundary issues, follow [SECURITY.md](SECURITY.md). Do not publish secrets, broker credentials or exploit details in a public issue.

---

## Disclaimer

This repository is research and engineering software. Backtests, model scores, diagnostics, target portfolios and generated artifacts are **not investment advice** and do not guarantee future performance.
