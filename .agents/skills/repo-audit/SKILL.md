---
name: repo-audit
description: Audit a qlib-platform branch, PR, or subsystem for architecture-boundary, data-integrity, research-governance, operations, contract, and validation risks; use for reviews or pre-PR evidence gathering.
---

# qlib-platform repository audit

Audit from evidence, not assumptions. This is a read-only workflow unless the user separately asks for a change.

1. Establish scope: current branch/diff, requested behavior, affected entry points, persistent outputs, and relevant data/research/operations phase.
2. Trace the smallest path from entry point through data/artifact flow to persistence, external delivery, or output. Identify the closest governing `AGENTS.md`, configuration, documentation, contracts, and tests.
3. Check the architecture boundary. This repository may ingest/bootstrap local or TuShare research data, publish immutable research-governance DataReleases, materialize Qlib datasets, run research/local signal operations, create monitoring feedback evidence, and publish Artifact Contract v2 research artifacts. It must not own broker/QMT writes, authoritative OMS/order/fill/position/ledger state, hard-risk enforcement, or authoritative LEAN execution semantics.
4. Check data invariants when applicable: source provenance, PIT visibility, adjustment/calendar semantics, immutable DataRelease/DatasetVersion identity, verification level, alias atomicity, migration safety, and capability enforcement.
5. Check research governance when applicable: labels, purge/embargo, fold isolation, OOS stitching, cache/checkpoint identity, final-holdout isolation, strategy/portfolio policy identity, and deterministic lineage.
6. Check operations when applicable: local deployment vs platform production ownership, health semantics, daily runner side effects, outbox idempotency/acknowledgement, ops-state mutation, feedback isolation, recovery, and secret handling.
7. Map each material finding to an invariant, file/symbol, impact, and existing or missing test. Report findings by severity, then residual risks and validation evidence.

For a complex audit with subagents, delegate independent read-only evidence gathering to `code_explorer`, `data_reviewer`, `research_reviewer`, `contract_reviewer`, `operations_reviewer`, or `test_reviewer`; keep synthesis and prioritization in the main agent. Do not delegate implementation by default.
