---
status: ACTIVE
owner: operations
applies_to_commit: 4f3f4369b6e55186967bc726bb8dd87fff0e5d70
last_verified: 2026-08-31
---

# Outbox Delivery

The Artifact Contract v2 outbox decouples immutable research publication from remote Platform availability.

## Lifecycle

```text
artifact-v2-export
    -> copy/verify immutable bundle
    -> enqueue local outbox item
    -> delivery attempt
    -> 2xx acknowledgement
    -> acknowledged local delivery state
```

Exporting a bundle does not mean it was delivered. Delivering a bundle does not mean Platform has granted `LEAN_VALIDATED`, `PAPER` or `PRODUCTION` state.

## Endpoint configuration

Supply the endpoint explicitly:

```powershell
& $RepoPython -m tushare_qlib outbox drain --endpoint <PLATFORM_ARTIFACT_ENDPOINT>
```

or configure `PLATFORM_ARTIFACT_ENDPOINT` in the process environment. Do not place endpoint secrets/tokens in documentation or logs.

## One-shot delivery

```powershell
& $RepoPython -m tushare_qlib outbox drain --endpoint <ENDPOINT>
& $RepoPython -m tushare_qlib outbox worker --endpoint <ENDPOINT> --once
```

Both forms perform a bounded worker cycle. The command reports acknowledged and pending counts.

## Long-running worker

```powershell
& $RepoPython -m tushare_qlib outbox worker `
  --endpoint <ENDPOINT> `
  --poll-seconds 30 `
  --max-poll-seconds 300
```

A long-running worker should be started only after the endpoint and polling policy are approved for that deployment.

## Retry contract

Retry the **same immutable payload**. Preserve:

- DataRelease binding;
- artifact graph parents;
- payload/artifact SHA-256 values;
- `externalRunId`;
- outbox item/idempotency identity.

Do not rebuild or rewrite a rejected bundle merely to obtain a different identity unless the underlying research input truly changed and a new governed artifact is warranted.

Only a successful HTTP 2xx response acknowledges an item. Network errors/non-2xx responses leave it pending/retryable.

## Inspect and recover

```powershell
& $RepoPython -m tushare_qlib ops-query --entity deliveries
& $RepoPython -m tushare_qlib ops-retry-delivery <IDEMPOTENCY_KEY>
```

`ops-retry-delivery` changes local recovery state; use the exact delivery idempotency key, not an arbitrary pipeline run ID.

If an operator acknowledgement is required:

```powershell
& $RepoPython -m tushare_qlib ops-ack `
  --entity delivery --id <DELIVERY_ID> `
  --operator <OPERATOR> --reason <REASON>
```

Acknowledgement records an audited human decision. It does not repair remote delivery, checksum drift or an invalid artifact graph.

Platform acknowledgement never transfers OMS, broker, hard-risk, order, fill or ledger ownership into this repository.
