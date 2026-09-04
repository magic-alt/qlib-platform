---
status: MOVED
owner: architecture
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-28
canonical_repository: magic-alt/platform
---

# QMT gateway moved to platform

The QMT read-only gateway is owned and operated by the sibling `platform`
repository. Its canonical implementation and runbook are now:

```text
platform/web/backend/app/broker/qmt_gateway
platform/docs/qmt_gateway.md
```

P3 physically removed `src/qlib_platform/qmt_gateway` and its installer/configuration
surface. Gateway changes and operational commands must be made in `platform`.

Qlib publishes `TARGET_PORTFOLIO`; platform owns QMT observations, hard risk,
orders, fills, reconciliation and ledger state.

When `platform` or QMT is unavailable, qlib-platform remains ready for local
authentication and research. Artifact Contract v2 output is retained in the local
platform-adapter outbox and execution capability is reported as degraded. Recovery
does not require restarting qlib-platform; an adapter may drain the immutable,
checksum-verified artifacts after platform returns.
