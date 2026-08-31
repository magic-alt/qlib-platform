---
status: ACTIVE
owner: operations
applies_to_commit: 4f3f4369b6e55186967bc726bb8dd87fff0e5d70
last_verified: 2026-08-31
---

# Model Lifecycle

This document describes **local Research Plane model operations**. Production approval, authoritative execution validation and production rollback remain owned by `magic-alt/platform`.

## Lifecycle overview

```text
research-run / train-select
        -> governed research evidence
        -> Research Gate / promotion evidence
        -> model-refit
        -> immutable local ModelRelease bundle
        -> model-deploy (local selection)
        -> live-inference / daily-signal-run
        -> local signal + health evidence
```

The currently active Phase 3-D program does not authorize candidate selection, final-holdout access or publishing. Generic model lifecycle commands must not be used to bypass those program-level restrictions; see [Current State](current_state.md).

## Research model

`train-select` and `research-run` create research evidence under a frozen model profile. Switching Ridge, LightGBM, XGBoost or PyTorch must not silently change DataRelease, DatasetVersion, FeatureSnapshot, AlphaPack, LabelSpec, SplitSpec or portfolio contracts.

Model-family changes are new research identities, not in-place edits to an existing result.

## Local ModelRelease refit

Exact syntax:

```text
model-refit --research-run <PROMOTED_WALK_FORWARD_RUN> --as-of <YYYY-MM-DD>
```

`model-refit` does **not** accept `--dataset-ref`. It first pins the DatasetVersion resolved by the selected configuration and then validates the supplied research release.

The refit path requires the research manifest to be:

- a walk-forward run;
- promotion status `PROMOTED`;
- promotion decision `PROMOTE`;
- complete in lineage;
- compatible with the configured AlphaPack, model profile fingerprint and canonical dataset/strategy/portfolio/risk recipe.

The refit plan preserves a label-safe validation tail, selects the approved training length/steps, fits the final model on the allowed window, records runtime/code/data lineage and creates an immutable model bundle. The bundle is registered locally; refit does not automatically select it for inference.

## Local deployment selection

```text
model-status
model-deploy <DEPLOYMENT_ID> [--device cpu]
model-rollback --to <DEPLOYMENT_ID> [--device cpu]
```

`model-status` reads the current local selection.

`model-deploy` and `model-rollback` mutate only the local ModelRegistry. Rollback selects a previously verified local ModelRelease for future inference; it does not mutate that artifact and does not change `platform` deployment/account state.

Do not describe these local transitions as production deployment approval.

## Live inference

Recommended governed form:

```text
live-inference --as-of <YYYY-MM-DD> --dataset-ref <DATASET_VERSION_REF> [--deployment-id <ID>]
```

When `--deployment-id` is omitted, the current local deployment is used. The command requires a deployment with local status `DEPLOYED`, loads and parity-checks its model bundle, pins the dataset, recomputes the live feature schema and evaluates signal health.

It writes a local immutable signal directory containing artifacts such as:

- `attestation.json`;
- `model_score.parquet`;
- `model_topk.csv`;
- `signal_health.json`;
- `manifest.json`.

A health rejection exits non-zero. A research/live parity comparison can be requested with `--compare-research` and may produce a distinct non-zero parity exit.

`--dataset-uri` exists as a lower-level compatibility override; governed operations should prefer a verified DatasetVersion ID/alias through `--dataset-ref`.

## Daily signal runner

```text
daily-signal-run --as-of <YYYY-MM-DD> [--no-notify] [--skip-sync] [--supersede]
```

Default flow:

1. create a local CLOSE pipeline run;
2. run `daily-sync` for the requested business date;
3. run live inference using the configured/pinned dataset and current local deployment;
4. persist signal health and local ops state;
5. optionally send a Feishu preview/rejection notification.

`--skip-sync` skips step 2 and removes the daily-sync requirement passed into signal health. `--no-notify` disables Feishu notification. `--supersede` explicitly permits replacement semantics in the local signal registry where supported.

The daily runner **does not automatically export Artifact Contract v2 or drain the platform artifact outbox**. Artifact export/delivery remains a separate governed workflow.

## Failure boundaries

Stop rather than repair in place when any of these drift:

- DatasetVersion/DataRelease binding;
- feature columns or processor recipe;
- model profile/fingerprint or bundle checksum;
- signal/trade date relationship;
- current deployment state;
- health/parity checks.

Use [Recovery](operations/recovery.md) for local recovery and escalate broker/QMT/order/fill/ledger incidents to `platform`.
