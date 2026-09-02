---
status: ACTIVE
owner: architecture
applies_to_commit: 85bac85356d8092adfe98cd82ee59f81a242cf53
last_verified: 2026-09-02
---

# Configuration Profiles

The CLI default is `configs/pipeline.standalone.yaml`. A workflow that consumes a Platform-produced DataRelease must select the integrated profile explicitly.

## Profile matrix

| Profile | Purpose | Platform required to load | TuShare required to load | Governed Phase 2/3 | Status |
| --- | --- | ---: | ---: | ---: | --- |
| `pipeline.standalone.yaml` | autonomous local research | no | no | capability dependent | DEFAULT |
| `pipeline.integrated.yaml` | Platform-produced DataRelease | no remote call merely to parse config; external release paths required for use | no | yes | ACTIVE |
| `pipeline.yaml` | integrated canonical/base config | external release paths required for use | no | yes | INTERNAL/COMPAT |
| `pipeline_phase2.yaml` | frozen Phase 2/3 governed profile | depends on bound release | no | yes | FROZEN |
| `pipeline_tushare_dev.yaml` | TuShare development/data download | no | credential only for API calls | no | DEV |
| `pipeline_lean_mysql.yaml` | legacy migration compatibility | legacy external source | no | no | DEPRECATED |

`pipeline.standalone.yaml` extends the TuShare-development base but overrides the mode, roots and release behavior so that simply loading the standalone application does not require `TUSHARE_TOKEN`.

`pipeline.integrated.yaml` extends `pipeline.yaml`; it is the explicit public profile for Platform-produced immutable releases.

## Selection rules

- Omit `--config` only when the standalone default is intended.
- Use `configs/pipeline.integrated.yaml` for external Platform DataRelease verification/materialization.
- Use `configs/pipeline_phase2.yaml` only where the frozen Phase 2/3 protocol explicitly requires it.
- Do not present `configs/pipeline.yaml` as the universal default.
- After DataRelease materialization, pin a DatasetVersion ID/alias for research and inference. Do not pass a DataRelease ID to `--dataset-ref`.
- A config change that alters governed data/model/portfolio semantics creates a new research identity; it does not rewrite prior evidence.

## Environment variables

The repository example environment file documents the supported roots/overrides. Important variables are:

| Variable | Scope | Purpose |
| --- | --- | --- |
| `QLIB_DATA_ROOT` | standalone | root for local releases, datasets, state, auth and outputs |
| `TUSHARE_TOKEN` | optional | needed only on machines that call TuShare APIs |
| `TUSHARE_CALLS_PER_MINUTE` | optional | TuShare client rate limit |
| `QLIB_REPO` | optional | source-checkout override used when Qlib source identity is required |
| `QLIB_DATA_URI` | optional/local qrun | explicit immutable Qlib provider path for supported local workflows |
| `QUANT_DATA_ROOT` | integrated | root containing the external Platform release and derived data |
| `DATASET_RELEASE_ID` | integrated | immutable DataRelease selected by the integrated profile |

`LEAN_MYSQL_*` / `LEAN_MYSQL_DSN` variables remain compatibility inputs for the deprecated migration profile; they are not the normal Research Plane architecture.

Never commit a populated `.env`, token, password or webhook secret. Documentation and logs may name an environment variable but must not expose its value.

## Installation extras

The base package contains the platform/core data-manifest dependencies. Optional extras are:

| Extra | Adds |
| --- | --- |
| `data` | TuShare, retry support and MySQL compatibility client |
| `qlib` | `pyqlib==0.9.7` and `lightgbm==4.6.0` |
| `pytorch` | PyTorch runtime |
| `xgboost` | XGBoost runtime |
| `all` | data + Qlib/LightGBM + XGBoost; **does not include PyTorch** |
| `dev` | Qlib/LightGBM/XGBoost plus pytest, coverage, Ruff, mypy and type stubs |
| `docs` | MkDocs Material documentation-site tooling (`mkdocs-material==9.7.7`) |

Examples:

```powershell
$RepoPython = '.\.venv\Scripts\python.exe'
& $RepoPython -m pip install -c constraints/ci.txt -e ".[dev]"
& $RepoPython -m pip install -c constraints/ci.txt -e ".[all,dev]"
& $RepoPython -m pip install -c constraints/ci.txt -e ".[dev,pytorch]"
& $RepoPython -m pip install -e ".[docs]"
```

The project pins `pyqlib==0.9.7` and `lightgbm==4.6.0`. A Windows OpenCL build must compile the same LightGBM version; it is not a separate dependency version profile.

The documentation dependency is deliberately separated from `dev` so ordinary research/development environments do not need the site generator. Build the site with:

```powershell
& $RepoPython -m mkdocs build --strict
```

## Safe profile smoke checks

Standalone:

```powershell
& $RepoPython -m tushare_qlib status
& $RepoPython -m tushare_qlib health ready
& $RepoPython -m tushare_qlib health dependencies
```

Integrated:

```powershell
& $RepoPython -m tushare_qlib --config configs/pipeline.integrated.yaml status
& $RepoPython -m tushare_qlib --config configs/pipeline.integrated.yaml release verify <DATA_RELEASE_REF>
```

A missing optional remote dependency may produce a degraded dependency result; an identity/checksum/configuration failure must not be relabeled as mere degradation.

## Runtime identity

Governed evidence records the selected DataRelease, DatasetVersion, FeatureSnapshot, AlphaPack, LabelSpec, SplitSpec, model profile, portfolio/research-backtest policy, code revision and relevant implementation hashes. Changing a profile to improve results creates a new research identity.

See [Identity and Lineage](identity_and_lineage.md) and [Testing and Certification](testing_and_certification.md).
