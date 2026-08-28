---
status: ACTIVE
owner: operations
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-28
---

# Incident Response

For a research-plane incident:

1. stop the affected state-changing job;
2. preserve manifests, checksums, logs and output directories;
3. record variable names only—never credential values;
4. classify the boundary: data identity, PIT/research isolation, model/runtime, artifact graph or delivery;
5. run the smallest read-only verifier;
6. escalate broker/QMT, hard-risk, LEAN, order/fill or ledger incidents to `magic-alt/platform`.

Do not open final holdout, rerun Phase 3 diagnosis, publish or deploy merely to diagnose an incident.
