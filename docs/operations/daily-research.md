---
status: ACTIVE
owner: operations
applies_to_commit: 4f3f4369b6e55186967bc726bb8dd87fff0e5d70
last_verified: 2026-08-31
---

# Daily Research

This runbook covers one local research/signal business date. It does not authorize broker execution or change the active Phase 3-D publishing restrictions.

## Preflight

```powershell
& $RepoPython -m qlib_platform status --json
& $RepoPython -m qlib_platform health ready
& $RepoPython -m qlib_platform health dependencies
& $RepoPython -m qlib_platform model-status
```

Before a governed run, verify the selected release and dataset independently:

```powershell
& $RepoPython -m qlib_platform release verify <DATA_RELEASE_REF> --mode deep
& $RepoPython -m qlib_platform dataset-resolve <DATASET_VERSION_REF>
& $RepoPython -m qlib_platform dataset-verify <DATASET_VERSION_REF> --mode deep
```

Confirm that the DatasetVersion is bound to the intended DataRelease and that the requested `as-of` date is a valid research/signal date.

## Option A: inference only

Use this when the data publication/sync requirement has already been satisfied:

```powershell
& $RepoPython -m qlib_platform live-inference `
  --as-of <YYYY-MM-DD> `
  --dataset-ref <DATASET_VERSION_REF> `
  --deployment-id <LOCAL_DEPLOYMENT_ID>
```

`live-inference` writes local score/TopK/health/manifest artifacts and signal state. Add `--require-daily-sync` when the signal contract must prove the local daily-sync state.

## Option B: daily orchestration

```powershell
& $RepoPython -m qlib_platform daily-signal-run --as-of <YYYY-MM-DD>
```

Default sequence:

1. start a local CLOSE pipeline run;
2. run `daily-sync`;
3. run live inference with the current local deployment;
4. persist signal/health/ops state;
5. optionally notify Feishu.

Use `--no-notify` to suppress the notification and `--skip-sync` only when skipping sync is explicitly intended. `--supersede` changes local signal-registry behavior and must be deliberate.

The daily runner does not automatically execute Artifact Contract v2 export or outbox delivery.

## Post-run checks

Inspect the returned signal manifest and then query local ops state:

```powershell
& $RepoPython -m qlib_platform ops-query --entity runs --business-date <YYYY-MM-DD>
& $RepoPython -m qlib_platform ops-summary --business-date <YYYY-MM-DD>
```

A `REJECTED` signal is evidence, not permission to loosen health gates. A `FAILED` run should be classified by phase (sync, inference, notification/delivery) before retry.

## State-changing boundaries

These require explicit authorization: `daily-sync`, `live-inference`, `daily-signal-run`, release/dataset promotion, model refit/deploy/rollback, Artifact v2 export and outbox delivery.

For failure recovery see [Recovery](recovery.md); for the full command surface see [CLI Reference](../cli_reference.md).
