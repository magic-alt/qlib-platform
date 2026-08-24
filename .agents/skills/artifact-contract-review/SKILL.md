---
name: artifact-contract-review
description: Review or change qlib-platform DataRelease, artifact, lineage, export, or TARGET_PORTFOLIO paths under Artifact Contract v2 and the platform handoff boundary.
---

# Artifact Contract v2 review

Read `docs/architecture_boundary.md`, `README.md` sections on Artifact Contract v2 and `TARGET_PORTFOLIO`, and the relevant artifact contract source/tests before judging or changing a contract path.

Verify that artifact schema, identity, payload checksum, lineage, and ownership agree across producer, manifest, validator, and tests. Trace these identities when relevant: `DataRelease`, `DatasetVersion`, `FeatureSnapshot`, `PredictionSnapshot`, `MODEL_RELEASE`, `STRATEGY_POLICY`, `SIGNAL_SNAPSHOT`, `TARGET_PORTFOLIO`, and `VALIDATION_RESULT`.

The cross-repository boundary is a content-addressed `TARGET_PORTFOLIO` in Artifact Contract v2 bound to exactly one immutable `DataRelease`. This repository can promote only through `RESEARCH_PROMOTED`. `platform` owns production DataRelease publication, authoritative LEAN semantics, hard risk, paper/shadow trading, OMS, QMT/broker interaction, orders, fills, ledger, and later lifecycle transitions.

Reject or flag any path that makes identity, schema, checksum, lineage, release binding, or validation optional. Favor explicit version changes over mutation; fail closed when a binding cannot be verified. Contract tests should cover both a valid round trip and the corresponding drift/tamper rejection path.
