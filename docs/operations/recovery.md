---
status: ACTIVE
owner: operations
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-28
---

# Recovery

Recovery is fail-closed:

1. capture the failing run/reference and error class without credential values;
2. verify DataRelease and DatasetVersion independently;
3. recheck model, FeatureSnapshot, PredictionSnapshot and Artifact v2 checksums as applicable;
4. use `ops-query` before any retry;
5. use `ops-retry-delivery` or `ops-ack` only with explicit authorization;
6. preserve the original immutable artifact and audit trail.

Do not repair by editing manifests, deleting outbox rows, changing IDs or copying newer data into an
existing immutable directory.
