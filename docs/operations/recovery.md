---
status: ACTIVE
owner: operations
applies_to_commit: 4f3f4369b6e55186967bc726bb8dd87fff0e5d70
last_verified: 2026-08-31
---

# Recovery

Recovery is fail-closed: identify the failed boundary, preserve immutable evidence, then retry the smallest safe operation.

## 1. Capture evidence

Record without credential values:

- selected config/profile;
- business/signal date;
- DataRelease and DatasetVersion references;
- local deployment/signal/run/delivery IDs as applicable;
- failing phase and exception/error code;
- manifest/checksum/log paths.

Do not delete or edit evidence before classification.

## 2. Classify the boundary

| Boundary | First checks |
| --- | --- |
| DataRelease | `release verify <REF> --mode deep` |
| DatasetVersion | `dataset-resolve`, `dataset-verify <REF> --mode deep` |
| Model | `model-status`, bundle/research lineage and deployment state |
| Live signal | signal manifest, health report, date/schema/parity checks |
| Pipeline run | `ops-query --entity runs ...` |
| Delivery/outbox | `ops-query --entity deliveries ...` |
| Broker/QMT/OMS/order/fill/ledger | stop local recovery and escalate to `magic-alt/platform` |

Verify DataRelease and DatasetVersion independently. A successful DatasetVersion check is not proof that the upstream release verification required by a governed workflow was performed.

## 3. Query state before retry

```powershell
& $RepoPython -m tushare_qlib ops-query --entity runs --business-date <YYYY-MM-DD>
& $RepoPython -m tushare_qlib ops-query --entity deliveries --status <STATUS>
```

For a delivery retry:

```powershell
& $RepoPython -m tushare_qlib ops-retry-delivery <IDEMPOTENCY_KEY>
```

Retry only after the reason the delivery is retryable is understood. Preserve the same immutable payload and identity.

## 4. Acknowledgement

When an audited acknowledgement is appropriate:

```powershell
& $RepoPython -m tushare_qlib ops-ack `
  --entity run --id <RUN_ID> `
  --operator <OPERATOR> --reason <REASON>
```

or use `--entity delivery` for a delivery record. Acknowledgement is a state record, not root-cause remediation.

## Prohibited recovery shortcuts

Do not recover by:

- editing a published DataRelease/DatasetVersion/FeatureSnapshot/PredictionSnapshot manifest;
- changing IDs, parent bindings or checksums;
- copying newer data into an existing immutable directory;
- deleting outbox/ops rows to make health green;
- silently changing an alias or deployment selection;
- opening final holdout or relaxing research/health gates;
- rerunning Phase 3 diagnosis merely as a smoke test.

If the immutable input is actually wrong, create a new governed version/release/run rather than modifying the old one.

See [Incident Response](incident-response.md) for incident handling and [Operations Runbook](../OPERATIONS_RUNBOOK.md) for normal procedures.
