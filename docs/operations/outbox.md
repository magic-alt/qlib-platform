---
status: ACTIVE
owner: operations
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-28
---

# Outbox Delivery

Artifact v2 bundles remain local until a configured adapter receives a successful acknowledgement.
`outbox drain` performs a bounded delivery attempt. `outbox worker --once` performs one worker cycle;
the long-running worker requires an explicitly approved endpoint and polling policy.

Retry the same immutable payload. Do not mutate its DataRelease binding, checksums, graph parents or
`externalRunId`. Platform acknowledgement does not transfer OMS, broker or ledger ownership here.
