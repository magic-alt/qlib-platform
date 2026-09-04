---
status: ACTIVE
owner: operations
applies_to_commit: 4f3f4369b6e55186967bc726bb8dd87fff0e5d70
last_verified: 2026-08-31
---

# Incident Response

This runbook covers Research Plane incidents. Execution-plane incidents involving broker/QMT, hard risk, LEAN, orders, fills, positions or ledger must be escalated to `magic-alt/platform`.

## Immediate actions

1. stop the affected state-changing job or worker;
2. preserve manifests, checksums, logs, output directories, ops/outbox state and the selected config identity;
3. record variable names/presence only—never credential values;
4. identify the earliest failed invariant rather than rerunning the entire pipeline;
5. use the smallest read-only verifier first;
6. keep final holdout, publishing and promotion restrictions unchanged during diagnosis.

## Classification

| Incident class | Examples | Primary evidence |
| --- | --- | --- |
| Identity/integrity | hash drift, wrong DataRelease/DatasetVersion binding | release/dataset manifest + verifier output |
| PIT/research isolation | causal timing, purge/embargo/fold overlap | research/acceptance evidence |
| Model/runtime | profile fingerprint, feature schema, bundle parity | model manifest, runtime probe, signal manifest |
| Local signal | invalid date, health rejection, duplicate/supersede conflict | signal health + local ops state |
| Artifact graph | missing parent, DataRelease mismatch, payload checksum | Artifact v2 bundle + source manifest |
| Delivery | endpoint failure, non-2xx, retryable outbox item | delivery/outbox state |
| Execution Plane | QMT/broker/OMS/hard risk/order/fill/ledger | escalate; do not reconstruct locally |

## Diagnostic commands

```powershell
& $RepoPython -m qlib_platform status --json
& $RepoPython -m qlib_platform health ready
& $RepoPython -m qlib_platform health dependencies
& $RepoPython -m qlib_platform ops-query --entity runs --business-date <YYYY-MM-DD>
& $RepoPython -m qlib_platform ops-query --entity deliveries
```

Add `release verify` and `dataset-verify` when the incident can involve data identity.

## Evidence handling

Preserve the original immutable artifact even if it is invalid. If a corrected artifact is required, publish a new governed identity and retain the rejected one for audit. Do not overwrite historical evidence or remove a failure from state merely to obtain a clean dashboard.

## Research-governance guardrails

Do not:

- open the final holdout to diagnose an infrastructure problem;
- rerun `phase3-diagnose` without authorization of its evidence output;
- create a formal candidate or change selection logic;
- publish/deploy merely to test whether an incident disappears;
- loosen Research Gate or signal-health thresholds as remediation.

## Closure

An incident is ready to close only when the failed invariant has a documented cause, the smallest relevant verifier passes on the repaired/new state, immutable evidence is preserved, and any retry/acknowledgement is recorded with the correct operator/reason.

See [Recovery](recovery.md) for retry procedures.
