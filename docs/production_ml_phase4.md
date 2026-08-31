---
status: ACTIVE
owner: operations
applies_to_commit: 169002a
last_verified: 2026-08-28
---

# Production ML Phase 4

Phase 4 moves the governed Research / Signal Plane toward a durable production batch ML system without
moving execution ownership out of `platform`. It is operational infrastructure work, not a successor
research program: Phase 3-D restrictions, sealed final holdout and publishing prohibition remain active.

## Implemented foundation

The first vertical slice is:

```text
verified PredictionSnapshot
        +
DataRelease-bound matured outcomes
        -> RealizedLabelSnapshot
        -> PredictionEvaluationSnapshot
        -> IC / RankIC / spread / rolling RankIC evidence
```

The snapshot writers use atomic publication, content-derived identities and payload checksums. Label
maturity is checked against the pinned trading calendar. Evaluation fails closed on parent checksum,
DataRelease/LabelSpec drift, missing prediction keys, small cross-sections or non-finite metrics.

Durable operations state now also records TaskRun attempts under PipelineRun, including status,
artifact reference and error code. A pipeline cannot finish while a task attempt remains RUNNING.

## Boundary and governance

- Artifact Contract v2 and `TARGET_PORTFOLIO` remain the sole execution-semantic outbound handoff.
- Feedback artifacts are immutable, non-authoritative research inputs/evidence; no orders, raw fills,
  broker state, holdings ledger or OMS writes are owned here.
- Feedback evaluation never performs candidate selection, holdout access, promotion or deployment.
- Current main remains `INCREMENTAL_REVALIDATION_REQUIRED`; this phase does not claim re-certification.

## Remaining gates

1. Integrate feature-level drift profiles and explicit SLI/SLO telemetry.
2. Wire the daily runner as a retryable DAG using PipelineRun/TaskRun state.
3. Define the platform-owned aggregate execution-feedback contract and tamper tests.
4. Add retraining triggers that create reviewable evidence only; keep promotion approval separate.
5. Add remote metadata/artifact durability, backup/restore drills, CD and supply-chain hardening.
6. Complete proportionate incremental revalidation before any production-readiness claim.
