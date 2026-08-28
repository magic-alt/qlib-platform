---
status: ACTIVE
owner: architecture
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-28
---

# Configuration Profiles

The CLI default is `configs/pipeline.standalone.yaml`. A command that needs Platform DataRelease input
must name an integrated profile explicitly.

| Profile | Purpose | Platform required | TuShare required | Phase 2/3 | Status |
| --- | --- | ---: | ---: | ---: | --- |
| `pipeline.standalone.yaml` | autonomous local research | no | only for downloads | capability dependent | DEFAULT |
| `pipeline.integrated.yaml` | Platform-produced DataRelease | yes | no | yes | ACTIVE |
| `pipeline.yaml` | integrated canonical/base config | yes | no | yes | INTERNAL/COMPAT |
| `pipeline_phase2.yaml` | frozen Phase 2/3 governed profile | depends on release | no | yes | FROZEN |
| `pipeline_tushare_dev.yaml` | TuShare development | no | yes for downloads | no | DEV |
| `pipeline_lean_mysql.yaml` | migration compatibility | external legacy source | no | no | DEPRECATED |

## Selection rules

- Omit `--config` only when the standalone default is intended.
- Use `configs/pipeline.integrated.yaml` for external DataRelease verification/materialization.
- Use `configs/pipeline_phase2.yaml` only where the governed Phase 2/3 protocol explicitly requires it.
- Do not present `configs/pipeline.yaml` as the universal default.
- Pin DatasetVersion ID/alias after DataRelease materialization. Do not pass DataRelease ID to
  `--dataset-ref`.

## Dependencies

```powershell
$RepoPython = '.\.venv\Scripts\python.exe'
& $RepoPython -m pip install -c constraints/ci.txt -e ".[dev]"
& $RepoPython -m pip install -e ".[all,dev]"
& $RepoPython -m pip install -c constraints/ci.txt -e ".[dev,pytorch]"
```

The project pins `pyqlib==0.9.7` and `lightgbm==4.6.0`. The Windows OpenCL build is not a separate
runtime version profile; it must compile the same LightGBM version.

## Runtime identity

Governed evidence must record the selected DataRelease, DatasetVersion, FeatureSnapshot, AlphaPack,
LabelSpec, SplitSpec, model profile, portfolio policy, code revision and relevant implementation hashes.
Changing a profile to improve results creates a new research identity; it does not mutate prior evidence.

## Secrets

Credentials remain in local environment variables. Documentation, logs, screenshots and artifacts may
name a variable such as `TUSHARE_TOKEN`, but must never contain or transmit its value.
