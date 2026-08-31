---
name: operations-change
description: Implement or review qlib-platform local model operations, live inference, daily signal, health, outbox, ops-state, recovery, authentication, or production-feedback changes without crossing into execution ownership.
---

# Research-plane operations change

Use this skill for local ModelRelease/refit/deployment selection, live inference, daily signal runs, health/readiness, authentication, durable outbox delivery, ops state/recovery, scheduling, and Production Feedback.

Read `docs/OPERATIONS_RUNBOOK.md`, the relevant `docs/operations/` page, `docs/model_lifecycle.md` or `docs/production_feedback.md`, and the implementation/tests for the changed path.

Preserve these boundaries and semantics:

- local `model-deploy`/`model-rollback` select a verified ModelRelease for future qlib-platform inference; they do not change platform Production deployment state;
- `daily-signal-run` is optional sync -> live inference -> local signal/health/ops state -> optional notification. It does not automatically export Artifact Contract v2 or drain the outbox;
- `artifact-v2-export` creates/verifies an immutable bundle and enqueues a durable local copy; enqueue is not delivery, delivery is not execution validation, and only a successful acknowledgement marks an outbox item delivered;
- outbox retries must reuse the same immutable payload/idempotency identity; do not mutate graph parents, DataRelease binding, checksums, or external run identity to make delivery succeed;
- `health live`, `health ready`, and `health dependencies` have different meanings. Platform unavailability is dependency degradation, not research-process liveness failure;
- ops acknowledgements/retries and scheduled-task changes are state-changing and require explicit intent, operator/reason data where required, and preserved audit history;
- Production Feedback (`REALIZED_LABEL_SNAPSHOT`, `PREDICTION_EVALUATION_SNAPSHOT`) is immutable monitoring evidence only and cannot automatically select, retrain, promote, deploy, publish, or access the sealed final holdout;
- broker/QMT writes, hard risk, OMS, authoritative orders/fills/positions/ledger, LEAN execution semantics, kill switches, and production rollback remain owned by `platform`.

Never log or persist credential values. Treat dates, deployment IDs, dataset references, endpoints, output paths, idempotency keys, and operator acknowledgements as explicit inputs rather than guessing them.

Tests should cover idempotency, duplicate/supersede behavior, failure persistence, health degradation, checksum/lineage rejection, notification/delivery failures, feedback binding/maturity, and recovery without mutation as relevant.
