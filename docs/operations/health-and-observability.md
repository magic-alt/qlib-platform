---
status: ACTIVE
owner: operations
applies_to_commit: 4f3f4369b6e55186967bc726bb8dd87fff0e5d70
last_verified: 2026-08-31
---

# Health and Observability

Health endpoints answer different questions. Do not collapse them into one boolean.

## Surfaces

| Command | Question | Typical blocking conditions |
| --- | --- | --- |
| `health live` | Is the process responsive? | process/runtime failure |
| `health ready` | Can this local Research Plane operate safely? | invalid config, registry corruption/incomplete schema, unusable filesystem, insufficient local readiness |
| `health dependencies` | Which data/optional external dependencies are available? | missing required data; TuShare/platform/adapter degradation reported by dependency |
| `status` / `status --json` | What mode, roots, release/dataset capabilities and local state resolved? | inspection surface; use it to explain readiness/dependency results |

Platform unavailability must not be reported as a research-process liveness failure. Conversely, checksum/identity drift, registry corruption or a failed local atomic-write/readiness probe must not be downgraded to “platform unavailable”.

## Baseline checks

```powershell
& $RepoPython -m tushare_qlib status --json
& $RepoPython -m tushare_qlib health live
& $RepoPython -m tushare_qlib health ready
& $RepoPython -m tushare_qlib health dependencies
```

Record the selected profile and resolved immutable references when comparing two machines; a different config/release/dataset can legitimately yield different dependency state.

## Operational state

Query runs and deliveries explicitly:

```powershell
& $RepoPython -m tushare_qlib ops-query --entity runs --business-date <YYYY-MM-DD>
& $RepoPython -m tushare_qlib ops-query --entity deliveries --status <STATUS>
& $RepoPython -m tushare_qlib ops-summary --business-date <YYYY-MM-DD>
```

`ops-query` requires `--entity`. `ops-summary` requires `--business-date`; with `--output` it also writes a report file.

## Interpretation rules

- **live healthy + ready healthy + dependency degraded**: local research can remain available when the degraded dependency is optional for the requested operation.
- **live healthy + not ready**: process runs, but local state is unsafe/incomplete; do not continue state-changing research operations.
- **identity/checksum verification failure**: fail closed and investigate the immutable input; do not “ack” it away.
- **notification/platform outage**: preserve local run/artifact state and retry only the affected delivery path.

Health output must never include credential values or secret endpoints. Use variable names and presence/degraded status only.

See [Recovery](recovery.md), [Outbox Delivery](outbox.md), and [Operations Runbook](../OPERATIONS_RUNBOOK.md).
