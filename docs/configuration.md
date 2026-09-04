---
status: ACTIVE
owner: architecture
applies_to_commit: f702bc80d27a92ab526dca630b168c99a15c95a5
last_verified: 2026-09-04
---

# Configuration Profiles

The default local-research profile is `configs/pipeline.standalone.yaml`.

For normal standalone use the configuration contract is:

> **Copy `.env.example` to `.env`. Do not edit YAML, DataRelease IDs, DatasetVersion IDs, or aliases.**

YAML profiles remain available as advanced overrides for integrated, governed, migration, or specialized workflows.

## Standalone zero-config contract

From the repository root:

```bash
cp .env.example .env
```

Windows:

```powershell
Copy-Item .env.example .env
```

The copied file is valid without editing:

```dotenv
QLIB_DATA_ROOT=./data
TUSHARE_CALLS_PER_MINUTE=180
TUSHARE_TOKEN=
QLIB_REPO=
QLIB_DATA_URI=
```

The default quickstart uses stable aliases internally:

```text
research-release-current
standalone-current
```

These are runtime lifecycle state, not environment configuration. Content-addressed `ds_*` and DatasetVersion IDs remain audit identities and should not be pasted into `.env` for standalone use.

## Profile matrix

| Profile | Purpose | Platform required | TuShare required | Normal user edits YAML? | Status |
| --- | --- | ---: | ---: | ---: | --- |
| `pipeline.standalone.yaml` | autonomous local research | no | only for download/refresh | no | **DEFAULT** |
| `pipeline.integrated.yaml` | external immutable DataRelease | external release paths required for use | no | advanced | ACTIVE |
| `pipeline.yaml` | integrated canonical/base config | external release paths required | no | internal | INTERNAL/COMPAT |
| `pipeline_candidate_research.yaml` | governed candidate workflow | depends on bound release | no | protocol-owned | FROZEN |
| `pipeline_tushare_dev.yaml` | TuShare development | no | credential for API calls | advanced | DEV |
| `pipeline_lean_mysql.yaml` | migration compatibility | legacy source | no | advanced | DEPRECATED |

`pipeline.standalone.yaml` extends the TuShare-development base only to reuse canonical research defaults. It clears inherited release binding, uses `data_source.kind=auto`, and does not require a TuShare credential merely to load or research existing local data.

## Environment variables

| Variable | Required? | Scope | Purpose |
| --- | ---: | --- | --- |
| `QLIB_DATA_ROOT` | default supplied | standalone | root for local releases, DatasetVersions, state and outputs |
| `TUSHARE_TOKEN` | no | standalone/data | only for TuShare API download/refresh |
| `TUSHARE_CALLS_PER_MINUTE` | default supplied | data | TuShare rate limit |
| `QLIB_REPO` | no | Qlib export | optional Qlib source-checkout override |
| `QLIB_DATA_URI` | no | standalone/qrun | optional existing Qlib provider override |
| `QUANT_DATA_ROOT` | integrated only | integrated | external Platform data root |
| `DATASET_RELEASE_ID` | integrated only | integrated | explicit immutable DataRelease |

`QLIB_REPO` and `QLIB_DATA_URI` are read by `Settings` when their YAML values are blank. This keeps the standalone YAML valid even when those optional variables are absent. When no Qlib source checkout is configured, the pinned `pyqlib==0.9.7` package provides the runtime identity and qlib-platform uses its packaged day-frequency dump compatibility path.

`LEAN_MYSQL_*` / `LEAN_MYSQL_DSN` remain migration compatibility inputs; they are not part of the normal standalone path.

Never commit populated secrets. Documentation/logs may name variables but must not print token/password values.

## Standalone lifecycle policy

The default release store uses:

```yaml
release_store:
  active_keep: 1
```

This does **not** delete immutable history. After successful standalone activation, older release directories are moved below `data/releases/archive/`, while the active release remains at the top-level release store. Exact archived IDs remain resolvable for audit/replay, and registry manifest paths are refreshed after the move.

When more than one active release exists, standalone resolution chooses the newest **materializable** release rather than blindly choosing the lexicographically latest hash. A materializable release must contain either the frozen `qlib_staging` component or an imported `qlib_dataset` provider.

Integrated mode deliberately keeps explicit multi-release selection fail-closed.

## Selection rules

- Omit `--config` when standalone is intended.
- Do not pass `ds_*` to `--dataset-ref`; that option accepts DatasetVersion IDs/aliases.
- Do not configure `research-release-current` or `standalone-current` in `.env`.
- Let the default quickstart self-prepare `standalone-current` when it is missing.
- Use `configs/pipeline.integrated.yaml` only for external immutable release workflows.
- Use candidate/governance profiles only when the protocol explicitly requires them.
- A semantic config change creates a new research identity; it does not rewrite prior evidence.

## Installation extras

| Extra | Adds |
| --- | --- |
| `data` | TuShare, retry support and MySQL compatibility client |
| `qlib` | `pyqlib==0.9.7` and `lightgbm==4.6.0` |
| `pytorch` | PyTorch runtime |
| `xgboost` | XGBoost runtime |
| `all` | data + Qlib/LightGBM + XGBoost; no PyTorch |
| `dev` | pytest, coverage, Ruff, mypy and research dependencies |
| `docs` | MkDocs Material tooling |

Full local research environment:

```bash
.venv/bin/python -m pip install -c constraints/ci.txt -e '.[all,dev]'
```

Optional PyTorch:

```bash
.venv/bin/python -m pip install -c constraints/ci.txt -e '.[dev,pytorch]'
```

## Safe checks

Standalone diagnostics:

```bash
bash scripts/run_local_research.sh doctor
.venv/bin/python -m qlib_platform status
.venv/bin/python -m qlib_platform health ready
```

The normal first research action does not require those checks:

```bash
bash scripts/run_local_research.sh run --alpha-pack alpha158_market_v1 --model lightgbm
```

Integrated diagnostics remain explicit:

```bash
.venv/bin/python -m qlib_platform --config configs/pipeline.integrated.yaml status
.venv/bin/python -m qlib_platform --config configs/pipeline.integrated.yaml release verify <DATA_RELEASE_REF>
```

## Runtime identity

Governed evidence still records the exact selected DataRelease, DatasetVersion, FeatureSnapshot, AlphaPack, LabelSpec, SplitSpec, model profile, portfolio policy, code revision and implementation hashes. Simplifying configuration does not weaken lineage; it removes lifecycle internals from normal user configuration.

See [Local Research Quickstart](local_research_quickstart.md), [Identity and Lineage](identity_and_lineage.md), and [Testing and Certification](testing_and_certification.md).
