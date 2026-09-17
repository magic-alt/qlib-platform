---
status: ACTIVE
owner: operations
applies_to_commit: 2b63bd64a19319a8e4579ca320299775aac9019b
last_verified: 2026-09-17
---

# Research Platform SRE: SLO, alerting, recovery, and game-day

This runbook is the operating contract for Issue #135. It covers research/data/platform SLO evidence only; it never authorizes model selection, retraining, promotion, deployment, or broker execution.

## SLO policy and versioning

The machine-readable policy lives at `configs/slo_policy.yaml` with schema `qlib-platform.slo-policy.v1`. The runtime hashes the selected profile plus `sre.policy_overrides` into an immutable `slo-*` version and binds the complete snapshot to the first DailyRun attempt. A resumed run therefore keeps the policy that originally governed it even if repository defaults later change.

Profiles are `dev`, `benchmark`, and `prod`. Production freshness minutes are intentionally **unset** in source control. `prod` fails closed until an operator calibrates a deadline from observed provider/update/runtime distributions and pins it through configuration. Do not invent a threshold during an incident.

Example calibrated override:

```yaml
sre:
  profile: prod
  policy_overrides:
    freshness:
      deadline_minutes: <calibrated-value>
```

Changing that value changes the SLO policy version and the DailyRun/RunManifest lineage.

## Read-only status and doctor

```bash
qlib-platform --config configs/pipeline.standalone.yaml doctor
qlib-platform --config configs/pipeline.standalone.yaml doctor --json
qlib-platform --config configs/pipeline.standalone.yaml sre status --json
```

Status is observational. It reports the recent target session, provider watermarks when available, active immutable release/DatasetVersion, latest DailyRun/SLO evidence, missed sessions, disk capacity, orphan temporary artifacts, active alerts/overrides, and blocking reasons. The status path must not create a registry, repair data, move aliases, or mutate checkpoints.

## Fail-closed consumption gate

The production DailyRun sequence is:

```text
plan -> audited/resumable sync -> DatasetVersion verify -> SLO gate -> frozen regression -> report
```

Required target-session freshness, required quality, calibrated production deadline, and required DatasetVersion verification are evaluated before research consumption. A BLOCKING failure writes SLO evidence, opens/deduplicates an incident, marks the DailyRun `BLOCKED`, and leaves regression/report delivery downstream blocked. Recovery resumes the same durable plan and immutable input; do not skip the gate by editing state files.

## Alert semantics

Alert fingerprints aggregate by `provider + session + failed_gate`, so a provider outage does not fan out into symbol-level alerts. Events use `INFO`, `WARN`, or `BLOCKING` and include run/session/release/provider/gate/reason/first action. Repeated evaluation of the same incident is deduplicated. When the gate passes again, a `RESOLVED` event is appended with the same incident correlation ID.

The append-only event ledger is `state/sre/events.jsonl`. Corruption is itself a blocking status condition; do not silently recreate it.

## Manual override

There is no invisible force bypass. An override requires operator identity, reason, and future expiry and creates an audit event:

```bash
qlib-platform --config configs/pipeline.standalone.yaml sre override   --gate platform.capacity   --operator <operator>   --reason "temporary storage migration"   --expires-at 2026-09-17T16:00:00+08:00   --session 20260917
```

Overrides are auditable evidence; they do not rewrite immutable artifacts or automatically convert a failing data/research gate into a passing result.

## Recovery playbooks

### Provider late / HTTP 429

1. Confirm `data.required_freshness` and provider/session correlation in `sre status`.
2. Preserve the SyncPlan and checkpoint ledger; do not create a replacement release from partial data.
3. Restore provider access/rate budget, then resume the same plan.
4. Require freshness/quality and DatasetVersion verification to pass before research consumes it.
5. Confirm a correlated `RESOLVED` event.

### Bad release or missing endpoint

1. Inspect `raw_validate`, `freshness_gate`, and endpoint-gap evidence.
2. Repair the source/staged input and resume; never hand-edit an immutable DataRelease/DatasetVersion manifest.
3. If the active release is known-good, keep it active until the replacement fully verifies.

### Corrupt Qlib/DatasetVersion artifact

1. Stop downstream research consumption.
2. Run immutable DatasetVersion verification and compare the manifest checksum.
3. Re-materialize from the certified parent release rather than patching binaries/features in place.
4. Replay and require artifact verification PASS plus `RESOLVED` evidence.

### Missed schedule / process kill / reboot

1. `doctor --json` identifies missed trading sessions from the local calendar.
2. Resume the durable plan/checkpoints for each missing session in order.
3. Use backfill only to advance beyond active immutable coverage; historical revisions use historical-audit mode.
4. Verify no duplicate alert fan-out and no regression against partial sessions.

### Capacity / disk full

1. Treat `platform.capacity` as blocking when a calibrated minimum-free policy is configured.
2. Free/expand storage without deleting immutable evidence needed by an active run.
3. Check orphan `*.tmp` artifacts and checkpoint integrity.
4. Resume the same plan and verify the incident resolves.

### Manual backfill

```bash
python -m qlib_platform.runtime.production_daily_run --backfill <START> <END>
```

Backfill remains fail-closed and never rolls the active DatasetVersion backward. Long backfills are an SRE schedule scenario, not permission to bypass freshness/quality checks.

## Game-day / fault injection

Supported deterministic fixtures:

- `provider-late`
- `provider-429`
- `schema-drift`
- `endpoint-missing`
- `qlib-corrupt`
- `disk-full`
- `process-kill`
- `pointer-crash`
- `alert-destination-unavailable`
- `long-backfill`

Example:

```bash
qlib-platform --config configs/pipeline.standalone.yaml sre game-day   --scenario provider-late   --output-dir ./data/output/sre-game-day
```

Each fixture emits evidence for failure -> alert -> repeated-evaluation dedupe -> recovery -> RESOLVED, and verifies incident correlation. The alert-destination scenario is WARN/degraded; data/integrity/capacity/recovery scenarios are blocking.

## Baseline metric drift

Baseline drift evidence can classify a metric as `PASS`, `INVESTIGATE`, or `REJECT`. The contract always records `model_action=NONE` and an empty automatic-action list. SRE monitoring must not trigger automatic model selection, retraining, promotion, or deployment.
