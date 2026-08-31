---
name: artifact-contract-review
description: Review or change qlib-platform DataRelease, artifact, lineage, export, feedback, or TARGET_PORTFOLIO paths under Artifact Contract v2 and the platform handoff boundary.
---

# Artifact and lineage review

Read `docs/architecture_boundary.md`, `docs/identity_and_lineage.md`, `docs/artifact_contract_v2.md`, `docs/current_state.md`, and the relevant contract source/tests before judging or changing an artifact path.

Verify schema, identity, payload checksum, lineage, parent edges, release capability, and ownership across producer, manifest, validator, exporter, consumer adapter, and tests. Trace the identities that actually participate in the changed path, including `DataRelease`, `DatasetVersion`, `FeatureSnapshot`, `PredictionSnapshot`, `MODEL_RELEASE`, `STRATEGY_POLICY`, `SIGNAL_SNAPSHOT`, `TARGET_PORTFOLIO`, `VALIDATION_RESULT`, `REALIZED_LABEL_SNAPSHOT`, and `PREDICTION_EVALUATION_SNAPSHOT`.

Do not collapse the two data-source modes. `qlib-platform` may publish immutable research-governance DataReleases from local/TuShare inputs and may also consume Platform-produced releases. A Platform verification/certification step must not silently change an existing release identity.

The only cross-repository artifact with execution semantics is a `TARGET_PORTFOLIO` in Artifact Contract v2 bound to exactly one immutable `DataRelease`. This repository can promote only through `RESEARCH_PROMOTED`. `platform` owns authoritative LEAN execution semantics, hard risk, paper/shadow/production trading, OMS, QMT/broker interaction, orders, fills, positions and ledger state, and later lifecycle transitions.

Production Feedback is a separate monitoring boundary. `REALIZED_LABEL_SNAPSHOT` and `PREDICTION_EVALUATION_SNAPSHOT` are not Artifact Contract v2 graph nodes and cannot themselves select, promote, deploy, or publish a model.

Reject or flag any path that makes identity, schema, checksum, lineage, release binding, capability checks, or validation optional. Do not overstate content addressing: verify the exact identity fields and payload checksums implemented by the artifact type. Favor explicit version changes over mutation and fail closed when a binding cannot be proven. Contract tests should cover a valid round trip and the corresponding drift/tamper rejection paths.
