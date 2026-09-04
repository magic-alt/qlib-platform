---
status: ACTIVE
owner: operations
applies_to_commit: f702bc80d27a92ab526dca630b168c99a15c95a5
last_verified: 2026-09-04
---

# Troubleshooting

For standalone research, start from the stable user-facing contract:

```bash
cp .env.example .env
bash scripts/run_local_research.sh run --alpha-pack alpha158_market_v1 --model lightgbm
```

Do not begin recovery by editing `ds_*`, DatasetVersion IDs, aliases, or YAML. Those are advanced lifecycle internals.

## `unknown dataset reference: standalone-current`

On the current standalone quickstart this is a recoverable preparation state, not a manual alias task.

The default `run` command will:

1. look for a matching existing DatasetVersion;
2. recover its alias when safe;
3. otherwise select the newest compatible active DataRelease;
4. materialize the frozen release into a DatasetVersion;
5. verify it;
6. continue the requested research command.

If the automatic prepare fails, run the read-only diagnostic:

```bash
bash scripts/run_local_research.sh doctor
```

Do not manually point Qlib at an unverified mutable directory to bypass the failure.

## `MATERIALIZE_REQUIRED`

For standalone mode this is normally an internal transition. A compatible DataRelease is materialized from its frozen `qlib_staging`/PIT components (or imported `qlib_dataset`) so the resulting DatasetVersion remains bound to that exact release.

If `QLIB_REPO` is blank, the supported `pyqlib==0.9.7` package is sufficient: qlib-platform uses its packaged day-frequency dump compatibility path and records the wheel identity in lineage.

If this state is returned to the user after the current quickstart, treat it as a defect or unsupported release profile rather than a request to hand-edit aliases.

## Multiple `ds_*` releases

Standalone mode keeps one active release by default. After successful activation, older immutable releases are moved below:

```text
data/releases/archive/
```

They are still exact-ID addressable for audit/replay, and the registry manifest path is refreshed when a release moves into the archive.

The resolver selects the newest release that can actually materialize a Qlib research dataset. It does not blindly choose a newer legacy profile that lacks `qlib_staging`/`qlib_dataset`.

Integrated mode intentionally remains fail-closed when several immutable releases require an operator decision.

## `RELEASE_SELECTION_REQUIRED`

This should normally appear only in integrated/advanced workflows. Inspect and promote explicitly there:

```bash
.venv/bin/python -m qlib_platform --config configs/pipeline.integrated.yaml release list
.venv/bin/python -m qlib_platform --config configs/pipeline.integrated.yaml release promote <DATA_RELEASE_ID> --alias research-release-current
```

Do not copy that integrated recovery procedure into normal standalone onboarding.

## `DATA_UNAVAILABLE`

No usable local provider/release/raw source was found.

Choose one of:

- place existing project data below `QLIB_DATA_ROOT`;
- set optional `QLIB_DATA_URI` in `.env` to an existing Qlib provider;
- set `TUSHARE_TOKEN` in `.env` so the bootstrap may download required data.

## `DATA_INCOMPATIBLE`

A DataRelease exists, but its profile cannot materialize the standalone Qlib research dataset. Use a release containing `qlib_staging`, import an existing Qlib provider, or rebuild from capable local raw inputs. Do not weaken the AlphaPack/release contract.

## Repository-local interpreter missing

Windows uses `.venv\Scripts\python.exe`; Linux/macOS uses `.venv/bin/python`. Recreate the local environment rather than silently substituting another environment.

## DataRelease passed to a dataset command

`release verify <DATA_RELEASE_REF>` validates a DataRelease. `dataset-resolve/show/verify` and `--dataset-ref` require a DatasetVersion ID/alias.

A `ds_*` release ID is evidence identity; it is not a DatasetVersion alias.

## Which verification mode should I use?

- `manifest`: metadata/inventory inspection;
- `sampled`: bounded deterministic routine integrity check;
- `deep`: all declared payloads; used where governed research/certification requires it.

The quickstart uses cheaper verification for discovery/doctor and retains deep verification for research execution.

## Dataset verification fails

Do not bypass verification. Repair/reimport/rebuild the governed source and rerun the quickstart. A stale receipt never authorizes changed bytes.

## Alpha158 Market works but Daily/PIT fails

The active DatasetVersion probably lacks additional daily/PIT fields or release components. Use a capable release/dataset rather than weakening the AlphaPack contract.

## qrun provider path fails on another machine

Set optional `QLIB_DATA_URI` in `.env` or use the immutable provider returned by `dataset-resolve`. Maintained workflows must not hard-code a workstation-specific absolute path.

## LightGBM OpenCL build changes dependency version

The project pin is `lightgbm==4.6.0`. A Windows OpenCL build must compile that exact version. A different version requires an explicit dependency/runtime change.

## `ops-query` says `--entity` is required

```text
ops-query --entity runs|deliveries [--business-date DATE] [--status STATUS]
```

## `ops-summary` says `--business-date` is required

```text
ops-summary --business-date YYYY-MM-DD [--output PATH]
```

## `ops-ack <RUN_ID>` does not work

Use:

```text
ops-ack --entity run|delivery --id <ID> --operator <NAME> --reason <TEXT>
```

Acknowledgement records an audited operator action; it does not repair the failure.

## `daily-signal-run` did not deliver Artifact v2

Expected. Daily signal generation and Artifact Contract v2 export/outbox delivery are separate workflows.

## `daily-signal-run` failed in notification

Inspect local run/signal evidence first. Notification failure must not erase a persisted immutable signal. Use `--no-notify` only for an intentional diagnostic rerun.

## Outbox endpoint is missing

`outbox drain/worker` requires `--endpoint` or `PLATFORM_ARTIFACT_ENDPOINT`. Missing delivery configuration is not permission to rewrite artifact identity.

## `model-refit` rejects the research run

Refit requires a compatible promoted walk-forward research release, complete lineage and the configured DatasetVersion. Do not use refit to bypass current governance restrictions.

## Phase command refuses a dirty checkout

Expected for frozen governed workflows. Do not weaken revision/implementation locks or governed date windows.

## Platform unavailable

Standalone local research remains independent. Durable export/outbox state may wait for the configured external endpoint; do not bypass checksum/lineage validation.

## Credential/config issue

Report only variable names, presence/absence and safe status. Never print or upload populated `.env` values, tokens, passwords or webhook secrets.

For multi-step operational recovery see [Recovery](operations/recovery.md) and [Operations Runbook](OPERATIONS_RUNBOOK.md).
