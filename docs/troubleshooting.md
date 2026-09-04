---
status: ACTIVE
owner: operations
applies_to_commit: 4f3f4369b6e55186967bc726bb8dd87fff0e5d70
last_verified: 2026-08-31
---

# Troubleshooting

## Repository-local interpreter missing

Windows standard `venv` layout requires `.venv\Scripts\python.exe`; Linux/macOS uses `.venv/bin/python`. If the repository-local interpreter is absent, recreate the environment rather than substituting an unrelated system Python.

## DataRelease passed to a dataset command

`release verify <DATA_RELEASE_REF>` validates a DataRelease. `dataset-resolve/show/verify` and governed `live-inference --dataset-ref` require a DatasetVersion ID/alias.

Resolve/materialize the dataset first; never relabel one ID as the other.

## Which verification mode should I use?

- `manifest`: explicit metadata/inventory inspection;
- `sampled`: bounded deterministic routine integrity check;
- `deep`: all declared payloads; use for governed research resolution, promotion, migration or certification where required.

`--reuse-receipt` does not authorize skipping current payload validation.

## `ops-query` says `--entity` is required

Current syntax requires:

```text
ops-query --entity runs|deliveries [--business-date DATE] [--status STATUS]
```

A historical example without `--entity` is invalid.

## `ops-summary` says `--business-date` is required

Use:

```text
ops-summary --business-date YYYY-MM-DD [--output PATH]
```

## `ops-ack <RUN_ID>` does not work

`ops-ack` has no positional run-id form. Use:

```text
ops-ack --entity run|delivery --id <ID> --operator <NAME> --reason <TEXT>
```

Acknowledgement records an audited operator action; it does not repair the failure.

## `daily-signal-run` did not deliver Artifact v2

Expected. The daily runner performs optional `daily-sync`, local live inference, local run/signal state and optional Feishu notification. Artifact Contract v2 export and outbox delivery are separate workflows.

Use `artifact-v2-export` only for an authorized research manifest, then use `outbox drain/worker` for delivery.

## `daily-signal-run` failed in notification

First inspect the local run/signal evidence. Notification is a separate phase and should not erase a successfully persisted immutable signal. If notifications are not required for a diagnostic rerun, `--no-notify` disables Feishu; do not expose `FEISHU_WEBHOOK_URL` or its secret while debugging.

## Outbox endpoint is missing

`outbox drain/worker` requires `--endpoint` or `PLATFORM_ARTIFACT_ENDPOINT`. A missing endpoint is a delivery configuration issue, not permission to rewrite/enqueue a new artifact identity.

## `model-refit` rejects the research run

The refit path requires a walk-forward research release with promotion status `PROMOTED`, decision `PROMOTE`, complete lineage and a canonical recipe/profile compatible with the selected configuration. It uses the DatasetVersion pinned by configuration and does not accept `--dataset-ref` directly.

Do not use model-refit to bypass active Phase 3-D publishing/selection restrictions.

## qrun provider path fails on another machine

Set `QLIB_DATA_URI` to the immutable path returned by `dataset-resolve`. Supported workflows use `{{ QLIB_DATA_URI }}`; a workstation-specific absolute provider path in a maintained workflow is a portability defect.

## LightGBM OpenCL build changes dependency version

The project pin is `lightgbm==4.6.0`. The Windows OpenCL build must compile that exact version. A different GPU dependency version requires a separately reviewed dependency/runtime update.

## Phase 3 command refuses a dirty checkout

Expected. Phase 3 locks a clean source revision and implementation hashes. Do not weaken the check or alter governed windows. `stability-diagnose` writes immutable evidence and is not a generic smoke test.

## Platform unavailable

Local research startup remains independent. Verified Artifact v2 bundles can remain in the durable outbox until the configured endpoint returns. Do not bypass checksum/lineage validation or delete delivery state to make health checks pass.

## Credential/config issue

Report only variable names, presence/absence, expected format or a safely derived non-secret status. Never print, paste, screenshot, log or upload credential values or populated `.env` contents.

For multi-step recovery use [Recovery](operations/recovery.md) and [Operations Runbook](OPERATIONS_RUNBOOK.md).
