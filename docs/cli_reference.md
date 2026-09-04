---
status: ACTIVE
owner: operations
applies_to_commit: 4f3f4369b6e55186967bc726bb8dd87fff0e5d70
last_verified: 2026-08-31
---

# CLI Reference

Invoke the CLI as:

```text
<repo-python> -m qlib_platform [--config PROFILE] COMMAND
```

The default profile is `configs/pipeline.standalone.yaml`. Prefer the repository-local interpreter and an explicit config whenever a workflow depends on integrated data or a frozen research protocol.

This page documents the operational contract of the most important commands. `--help` remains authoritative for complete parser syntax.

## Side-effect classes

| Class | Meaning | Examples |
| --- | --- | --- |
| Read-only | does not intentionally publish/modify governed state | `status`, `health ...`, `release list`, `model-status` |
| Verification-first | reads payloads and may write an explicit verification/report artifact | `release verify`, `dataset-verify`, `project-audit`, `research-audit`, `stability-portable-verify` |
| Local state-changing | publishes data/artifacts or changes local registry/deployment/ops state | `dataset-build`, `dataset-promote`, `model-refit`, `model-deploy`, `live-inference`, `daily-signal-run` |
| External delivery/integration | may send or register immutable artifacts outside this process | `outbox drain`, `outbox worker`, `lean-register` |

A command that writes an explicitly named output is still a write even when its computation is diagnostic.

## Runtime and health

```powershell
& $RepoPython -m qlib_platform status
& $RepoPython -m qlib_platform status --json
& $RepoPython -m qlib_platform health live
& $RepoPython -m qlib_platform health ready
& $RepoPython -m qlib_platform health dependencies
```

- `health live` checks process responsiveness only.
- `health ready` checks local readiness under the selected profile.
- `health dependencies` reports data and optional external dependency state.
- Platform/TuShare degradation is not automatically a process-liveness failure.

## DataRelease

```text
release list
release verify <REFERENCE> [--mode manifest|sampled|deep] [--sample-size N] [--reuse-receipt] [--workers N]
release import-qlib --path <QLIB_PROVIDER>
release build-local [--start DATE --end DATE]
release build-tushare --start DATE --end DATE
release promote <REFERENCE> [--alias research-release-current]
```

`list` and `verify` are inspection/verification commands. Import/build/promote commands publish or move local governed state and require explicit source/date/alias authorization.

`manifest`, `sampled` and `deep` are distinct verification strengths; governed research/promotion/certification should use the level required by its protocol, normally `deep`.

## DatasetVersion

```text
dataset-list [--name NAME]
dataset-show <REFERENCE>
dataset-resolve [REFERENCE]
dataset-verify <REFERENCE> [--mode manifest|sampled|deep] [--sample-size N] [--reuse-receipt] [--workers N]
dataset-promote <REFERENCE> [--alias research-current]
registry-rebuild [--root DATA_ROOT]
```

`--dataset-ref` consumers expect a DatasetVersion ID/alias, not a DataRelease ID. `dataset-verify` validates DatasetVersion identity/partitions; it does not replace independent `release verify` when the workflow requires upstream release verification.

## Research and diagnostics

Core research artifact commands include:

`feature-store`, `train-select`, `research-run`, `backtest-predictions`, `research-report`, `alpha-diagnose`, `regime-diagnose`, `attribution-diagnose`, `explanation-diagnose`, `build-target-portfolio`, `research-gate`, `artifact-v2-export` and `lean-register`.

These commands create, register or deliver evidence. Confirm immutable inputs and explicit outputs before execution.

Phase 3-D exposes:

- `stability-validate`;
- `stability-plan`;
- `stability-diagnose`;
- `stability-portable-export`;
- `stability-portable-verify`.

`stability-validate`, `stability-plan`, `stability-diagnose` and `stability-portable-export` write explicitly named evidence. `stability-portable-verify` is the cross-machine read-only verifier. Phase 3-D exposes no candidate-selection, final-holdout-open or publishing command.

## Local model lifecycle

```text
model-status
model-refit --research-run <PROMOTED_WALK_FORWARD_RUN> --as-of <YYYY-MM-DD>
model-deploy <DEPLOYMENT_ID> [--device cpu]
model-rollback --to <DEPLOYMENT_ID> [--device cpu]
```

`model-refit` consumes the DatasetVersion pinned by the selected configuration and a promoted, complete-lineage walk-forward research release. It does not accept a `--dataset-ref` option itself.

`model-deploy` and `model-rollback` change only the local ModelRegistry selection. They do not modify `platform` production state.

## Live inference and daily signal

```text
live-inference --as-of <YYYY-MM-DD> [--deployment-id ID] [--dataset-ref VERSION_OR_ALIAS]
               [--require-daily-sync] [--supersede]
               [--compare-research PATH] [--parity-output PATH]

daily-signal-run --as-of <YYYY-MM-DD> [--no-notify] [--skip-sync] [--supersede]
```

`live-inference` writes a local immutable live-signal directory containing score, TopK, health and manifest artifacts and registers signal state. It is not read-only.

`daily-signal-run` uses the configured/pinned dataset and local deployed model. By default it runs `daily-sync`, then live inference, then optionally sends a Feishu notification. It writes local run/signal state. It does **not** automatically perform Artifact Contract v2 export or drain the artifact outbox.

`--dataset-uri` exists as a lower-level compatibility override for `live-inference`; governed workflows should prefer an immutable DatasetVersion through `--dataset-ref`.

## Production feedback

```text
feedback-build-labels \
  --labels <PARQUET> --calendar <CALENDAR_FILE> --observed-through <DATE> \
  --data-release-id <ID> --label-spec-id <ID> \
  --horizon-days <N> --signal-lag-days <N> \
  --source-artifact-id <ID> --output <OUTPUT>

feedback-evaluate \
  --predictions <PREDICTION_SNAPSHOT> \
  --realized-labels <REALIZED_LABEL_SNAPSHOT> \
  --output <OUTPUT> [--topk 50] [--min-cross-section 20] [--rolling-window 20]
```

Both commands create immutable monitoring evidence. `feedback-evaluate` exits non-zero when the generated evaluation decision is not `PASS`. Neither command selects, promotes, deploys or publishes a model.

See [Production Feedback](production_feedback.md).

## Artifact export and outbox

```text
artifact-v2-export <RESEARCH_MANIFEST> --output-dir <DIR> --git-commit <SHA> --container-digest <DIGEST> [--data-release-id ID]
outbox drain [--endpoint URL] [--timeout-seconds N]
outbox worker [--endpoint URL] [--timeout-seconds N] [--poll-seconds N] [--max-poll-seconds N] [--once]
```

`artifact-v2-export` writes a bundle and enqueues it locally. Network delivery is a separate outbox operation. `PLATFORM_ARTIFACT_ENDPOINT` may supply the endpoint when `--endpoint` is omitted.

## Operations state

The exact syntax is:

```text
ops-query --entity runs|deliveries [--business-date YYYY-MM-DD] [--status STATUS]
ops-summary --business-date YYYY-MM-DD [--output PATH]
ops-retry-delivery <IDEMPOTENCY_KEY>
ops-ack --entity run|delivery --id <ID> --operator <NAME> --reason <TEXT>
```

`ops-query` and `ops-summary` are read/query surfaces, except that `ops-summary --output` writes the requested report file. `ops-retry-delivery` changes local delivery recovery state. `ops-ack` records an explicit operator acknowledgement and must not be used as a substitute for repairing the underlying failure.

## Other state-changing commands

- ingestion/build: `backfill`, `backfill-extended`, `sync-*`, `daily-sync`, `curate*`, `stage-*`, `dump-*`, `dataset-build`, `migrate-qlib-layout --apply`, `registry-rebuild`;
- release/aliases: `release import-qlib`, `release build-local`, `release build-tushare`, `release promote`, `dataset-promote`;
- auth/bootstrap: `bootstrap`, `auth bootstrap-admin`, `auth user-create`;
- model/inference: `model-refit`, `model-deploy`, `model-rollback`, `live-inference`, `daily-signal-run`;
- delivery/ops: `outbox drain`, `outbox worker`, `ops-retry-delivery`, `ops-ack`;
- feedback artifacts: `feedback-build-labels`, `feedback-evaluate`.

Authorize the exact date windows, references, aliases, deployment IDs, endpoints and output paths applicable to the command.
