---
status: ACTIVE
owner: operations
applies_to_commit: e592bf8107dde383bcd7e45f9daf72c921dd122b
last_verified: 2026-09-16
---

# Daily Research

This runbook covers the fail-closed daily Research Plane operation introduced by Issue #129. It publishes research data and evidence only. It does not authorize broker execution, automatically promote models, or unseal a research holdout.

## Production entrypoint

The scheduled production operation is `tq-daily-run`:

```powershell
& $RepoPython -m qlib_platform.runtime.production_daily_run --as-of <YYYY-MM-DD>
```

An installed wheel exposes the equivalent command:

```text
tq-daily-run --as-of <YYYY-MM-DD>
```

The routine DAG is:

`local calendar plan -> durable market staging -> targeted adj_factor reconciliation -> dividend staging -> atomic Bronze promotion -> raw quality gate -> extended/PIT refresh -> metadata/freshness gate -> immutable DataRelease/DatasetVersion publication -> DatasetVersion verification -> optional frozen regression -> report/notification`

Required data failure is fail closed. No downstream regression or signal workflow may treat yesterday's data as today's successful input.

## Network-free plan

`--plan` reads the local trade calendar, partition manifests and watermarks only. It does not create a TuShare client and does not require `TUSHARE_TOKEN`:

```powershell
tq-daily-run --as-of 2026-09-16 --plan
```

The plan records the target exchange session, endpoint-specific gaps, bounded refresh window, local watermarks, config hash and staging root. A non-trading session ends as `SKIPPED_NON_TRADING_DAY`.

Do **not** run `daily-sync --check-only` before every production daily run. That legacy diagnostic performs provider work and therefore is not the production planning contract.

## Apply and resume

Normal execution creates and immediately applies a durable plan:

```powershell
tq-daily-run --as-of 2026-09-16
```

For controlled operation, plan first and then resume the returned plan ID:

```powershell
tq-daily-run --as-of 2026-09-16 --plan
tq-daily-run --resume <SYNC_PLAN_ID>
```

Provider responses are persisted under `data/staging/daily_sync/<plan_id>/` before they are consumed. Market partitions, factor histories and dividend calls that already have a terminal staged artifact are reused after a crash instead of being downloaded again. Canonical Bronze and active DatasetVersion/DataRelease aliases change only after their respective validation gates pass.

The daily sync state is under:

- `data/state/daily_sync/plans/<plan_id>/plan.json`
- `data/state/daily_sync/plans/<plan_id>/apply_state.json`
- `data/state/daily_sync/watermarks.json`
- `data/state/daily_sync/factor_index/`
- `data/state/daily_sync/pending_publish.json`

The top-level DailyRun evidence is under `data/state/daily_run/runs/<plan_id>/` and contains `run_state.json`, `manifest.json` and `report.md`.

## Incremental adj-factor contract

Routine daily operation does not rebuild every historical full-market `adj_factor` partition for every corporate-action symbol. A symbol-oriented factor index records `indexed_through` and logical history. When an event is detected:

1. stale symbol indexes are caught up in one pass over only the canonical dates after their watermark;
2. the provider symbol history is fetched at most once per durable plan and persisted to staging;
3. local/remote histories are vector-diffed;
4. only trade dates whose factor row/value actually changed are patched.

Full-history inspection is separated from the routine path:

```powershell
tq-daily-run --as-of <YYYY-MM-DD> --mode historical-audit
```

Use bootstrap/full rebuild commands for first-time database construction. Do not turn a full audit into the normal daily schedule.

## Backfill

Explicit historical catch-up uses exchange-open sessions from the local versioned calendar:

```powershell
tq-daily-run --backfill 2026-09-02 2026-09-15
```

Backfill publishes immutable historical evidence and does not run the optional daily regression or emit a future-session signal. Existing historical releases are not overwritten in place.

## Frozen regression

The daily DAG supports an immutable-dataset regression node. It is deliberately disabled in the standalone default until the separate Qlib/TuShare official-parity baseline is certified. To execute it explicitly:

```powershell
tq-daily-run --as-of <YYYY-MM-DD> --regression
```

The node receives the exact published DatasetVersion ID, not a mutable `latest` path, and invokes the governed reference baseline. It may validate a frozen/reference strategy; it must not select, promote or deploy a model based on that day's result.

## Scheduler source of truth

`production.daily_run.schedule` in `configs/pipeline.standalone.yaml` is the canonical clock. `tq-render-scheduler` renders systemd and launchd definitions from it; do not edit generated clock values independently.

```yaml
production:
  daily_run:
    schedule:
      time: "18:30"
      timezone: Asia/Shanghai
```

systemd receives the configured timezone explicitly. launchd uses the configured hour/minute on the macOS host, so the host timezone must match the configured production timezone.

## Recovery procedures

For a missed run, run `--plan` for the missed session and then `--resume`; use `--backfill` for multiple sessions. For a provider-late run, keep the failed/blocked plan as evidence and create a new plan after the provider is complete unless the existing plan failed before the affected provider response was staged. For a process kill, use `--resume <plan_id>`; validated stage artifacts are reused. For disk-full, free capacity without deleting the current immutable release, verify staged/canonical manifests, then resume. For a corrupt staged artifact, delete only the affected plan's staging partition and resume so it is fetched again. For a corrupt shared factor index, remove only the affected symbol index/parquet pair; the next event rebuilds that symbol from canonical factor partitions. Never repair an already published DataRelease/DatasetVersion in place.

## Signal generation remains a separate authority boundary

`daily-signal-run` and `live-inference` remain available for the production signal lifecycle, but they are not the canonical data/research scheduler introduced by #129. A signal operation must consume a successfully published/verified immutable dataset and preserve its model/deployment identity.

Example inference-only operation:

```powershell
& $RepoPython -m qlib_platform live-inference `
  --as-of <YYYY-MM-DD> `
  --dataset-ref <DATASET_VERSION_REF> `
  --deployment-id <LOCAL_DEPLOYMENT_ID>
```

## Post-run checks

Inspect the DailyRun manifest and report, then independently verify the immutable dataset if deeper evidence is required:

```powershell
& $RepoPython -m qlib_platform dataset-resolve <DATASET_VERSION_REF>
& $RepoPython -m qlib_platform dataset-verify <DATASET_VERSION_REF> --mode deep
```

A `BLOCKED`, `FAILED` or `REJECTED` artifact is evidence, not permission to loosen a freshness, quality, research or signal gate.

For general incident handling see [Recovery](recovery.md); for the command surface see [CLI Reference](../cli_reference.md).
