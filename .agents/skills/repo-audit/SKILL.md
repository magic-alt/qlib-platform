---
name: repo-audit
description: Audit a qlib-platform branch, PR, or subsystem for architecture-boundary, research-governance, contract, and validation risks; use for reviews, audits, or pre-PR evidence gathering.
---

# qlib-platform repository audit

Audit from evidence, not assumptions. This is a read-only workflow unless the user separately asks for a change.

1. Establish scope: current branch/diff, requested behavior, affected entry points, and relevant artifact or research phase.
2. Trace the smallest path from entry point through data/artifact flow to its persistence or output boundary. Identify the closest governing `AGENTS.md`, configuration, documentation, and tests.
3. Check the architectural boundary: this repository may publish research artifacts, including a content-addressed `TARGET_PORTFOLIO` bound to one `DataRelease`; it must not own production ingestion, broker/QMT state, OMS, orders, fills, ledger, hard risk, or authoritative LEAN execution semantics.
4. Check research governance when applicable: PIT timing, labels, split/purge/embargo semantics, fold isolation, OOS stitching, cache and checkpoint identity, final-holdout isolation, and deterministic lineage.
5. Map each material finding to an invariant, a file/symbol, impact, and existing or missing test. Report findings by severity, then residual risks and validation evidence.

For a complex audit with available subagents, delegate independent read-only evidence gathering to `code_explorer`, `research_reviewer`, `contract_reviewer`, or `test_reviewer`; keep synthesis in the main agent. Do not delegate implementation by default.
