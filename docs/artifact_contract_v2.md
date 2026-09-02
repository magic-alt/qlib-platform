---
status: ACTIVE
owner: architecture
applies_to_commit: 4f3f4369b6e55186967bc726bb8dd87fff0e5d70
last_verified: 2026-08-31
---

# Artifact Contract v2

Artifact Contract v2 is the sole governed qlib-to-platform publication boundary. It transfers research intent and validation evidence, not broker state.

The graph contains:

1. `MODEL_RELEASE`;
2. `STRATEGY_POLICY`;
3. `SIGNAL_SNAPSHOT`;
4. `TARGET_PORTFOLIO`;
5. `VALIDATION_RESULT`.

```text
MODEL_RELEASE
    -> STRATEGY_POLICY
    -> SIGNAL_SNAPSHOT
    -> TARGET_PORTFOLIO
    -> VALIDATION_RESULT
```

Each artifact belongs to one graph, has explicit parents, carries a payload reference/checksum and is bound to the same immutable DataRelease. `VALIDATION_RESULT` closes the research validation graph; `TARGET_PORTFOLIO` is the only artifact with execution-semantic handoff meaning.

## Ownership

`qlib-platform` may create research artifacts and promote no further than `RESEARCH_PROMOTED`.

`platform` validates ingestion into its own boundary and owns authoritative LEAN semantics, hard risk, OMS, QMT/broker connectivity, orders, fills, positions and ledger together with `LEAN_VALIDATED`, `PAPER`, `PRODUCTION` and `RETIRED` transitions.

A successful Artifact v2 delivery therefore does **not** mean the strategy is production-approved, an order was submitted, or a broker/account state changed.

## Publication gates

A bundle must be rejected when:

- artifact schema or a required parent is missing;
- payload SHA-256 differs from the declared checksum;
- graph artifacts bind different DataRelease IDs;
- source research-manifest lineage is absent or mismatched;
- promotion state exceeds qlib ownership;
- a lower-capability release is used for a handoff it does not authorize;
- `MODEL_TOPK`, simulated orders or audit rows are substituted for `TARGET_PORTFOLIO`.

The local exporter currently carries DatasetVersion/FeatureSnapshot/PredictionSnapshot lineage through the source research manifest rather than separate v2 nodes. Consumers must not invent or infer missing graph nodes.

## Export and delivery lifecycle

Export and network delivery are separate operations.

```text
research manifest
    -> artifact-v2-export
    -> verified local bundle
    -> durable local outbox
    -> outbox drain / worker
    -> platform acknowledgement
```

`artifact-v2-export` verifies the release capability, writes the v2 bundle and enqueues a durable outbox item. It does not require the remote endpoint to be available at export time.

`outbox drain` performs one bounded delivery cycle; `outbox worker` may retry repeatedly. Only a successful 2xx response acknowledges an item. Retries reuse the same immutable payload/idempotency identity; do not rewrite graph parents, checksums, `externalRunId` or DataRelease binding to force acceptance.

`lean-register` is a compatibility/integration command that can call an external registration endpoint. It is not authoritative LEAN validation and must not be interpreted as a transition to `LEAN_VALIDATED`.

See [Outbox Delivery](operations/outbox.md) and [CLI Reference](cli_reference.md).

## Production feedback boundary

`REALIZED_LABEL_SNAPSHOT` and `PREDICTION_EVALUATION_SNAPSHOT` are local Production ML feedback artifacts. They are **not** Artifact Contract v2 graph nodes and do not change the sole execution-semantic handoff.

A realized-label snapshot binds a DataRelease, LabelSpec, source artifact and pinned trading-calendar observation cut. An evaluation snapshot binds exactly one verified PredictionSnapshot to one compatible realized-label snapshot. Both fail closed on parent/binding/payload drift and remain monitoring evidence only.

See [Production Feedback](production_feedback.md).

## Vendored schemas

The vendored schema provenance and file digests are recorded in the repository [contracts README](https://github.com/magic-alt/qlib-platform/blob/main/contracts/README.md). Schema conformance, payload verification and governance checks are distinct layers; the presence of a vendored schema does not by itself prove that an export path has run every governed validation.
