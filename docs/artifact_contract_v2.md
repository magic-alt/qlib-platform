---
status: ACTIVE
owner: architecture
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-28
---

# Artifact Contract v2

Artifact Contract v2 is the sole qlib-to-platform publication boundary. The bundle contains:

1. `MODEL_RELEASE`;
2. `STRATEGY_POLICY`;
3. `SIGNAL_SNAPSHOT`;
4. `TARGET_PORTFOLIO`;
5. `VALIDATION_RESULT`.

Each artifact belongs to one graph, has explicit parents, carries a payload reference/checksum and is
bound to the same immutable DataRelease. `VALIDATION_RESULT` is the graph root; the
`TARGET_PORTFOLIO` is the only execution-semantic handoff.

## Ownership

`qlib-platform` may create research artifacts and promote no further than `RESEARCH_PROMOTED`.
`platform` validates ingestion and owns `LEAN_VALIDATED`, Paper, Production and later lifecycle
states, along with hard risk, OMS, QMT/broker, orders, fills and ledger.

## Publication gates

A bundle must be rejected when:

- artifact schema or required parent is missing;
- payload SHA-256 differs;
- artifacts bind different DataRelease IDs;
- source research manifest lineage is absent or mismatched;
- promotion state exceeds qlib ownership;
- `MODEL_TOPK`, simulated orders or audit rows are substituted for `TARGET_PORTFOLIO`.

The local exporter currently carries DatasetVersion/FeatureSnapshot/PredictionSnapshot lineage through
the source manifest rather than separate v2 nodes. Consumers must not infer missing graph nodes.

## Feedback boundary

`REALIZED_LABEL_SNAPSHOT` and `PREDICTION_EVALUATION_SNAPSHOT` are local Production ML feedback
artifacts. They are not Artifact Contract v2 graph nodes and do not change the sole execution-semantic
handoff. A realized-label snapshot binds a DataRelease, LabelSpec, source artifact and trading-calendar
observation cut; an evaluation snapshot binds that artifact to exactly one verified PredictionSnapshot.
Both are content-addressed and fail closed on binding or payload drift.

## Feedback boundary

`REALIZED_LABEL_SNAPSHOT` and `PREDICTION_EVALUATION_SNAPSHOT` are local Production ML feedback
artifacts. They are not Artifact Contract v2 graph nodes and do not change the sole execution-semantic
handoff. A realized-label snapshot binds a DataRelease, LabelSpec, source artifact and trading-calendar
observation cut; an evaluation snapshot binds that artifact to exactly one verified PredictionSnapshot.
Both are content-addressed and fail closed on binding or payload drift.

## Vendored schemas

The vendored schema provenance and file digests are recorded in
[contracts/README.md](../contracts/README.md). Schema conformance, payload verification and governance
checks are distinct layers; the presence of a vendored schema does not by itself prove that every
export path has run full schema validation.
