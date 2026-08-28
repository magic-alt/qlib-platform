---
status: ACTIVE
owner: operations
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-28
---

# Daily Research

1. Run `status` and `health dependencies`.
2. Verify the selected DataRelease with `release verify`.
3. Resolve/show/verify the bound DatasetVersion.
4. Confirm model status and the intended `as-of` date.
5. Run inference or `daily-signal-run` only after authorizing its output.
6. Inspect the local outbox and delivery acknowledgement.

`daily-sync`, `daily-signal-run`, release promotion, model deployment and outbox delivery are
state-changing. Use explicit dates and references; report affected outputs.
