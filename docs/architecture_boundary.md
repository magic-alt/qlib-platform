---
status: ACTIVE
owner: architecture
applies_to_commit: 86b0bac9bd9f2d54fd2a2654b8196eb7d6a54639
last_verified: 2026-09-15
---

# Architecture Boundary

`qlib-platform` is an autonomous Research / Alpha Factory with its own lifecycle. It consumes or publishes an immutable `DataRelease`, builds features and models, performs governed research, and publishes only governed research artifacts through Artifact Contract v3 while retaining explicit v2 compatibility. `platform` / `lean-local-platform` is an optional Execution Plane, not a startup, authentication, or local-research dependency.

## Ownership matrix

| Concern | qlib-platform | execution platform |
| --- | --- | --- |
| Local/TuShare/provider-neutral research data bootstrap | owns | no |
| DataRelease publication | research-governed releases | may publish/certify execution-authoritative releases |
| Qlib DatasetVersion materialization | owns | no |
| PIT features, factors, model training/tuning | owns | no |
| IC/RankIC, walk-forward, research backtests | owns | may independently perform authoritative LEAN validation |
| Research portfolio simulation | owns, inside Qlib only | no |
| `TARGET_PORTFOLIO` construction | owns | consumes/verifies |
| Artifact Contract v3 | exports; v2 previous supported | independently ingests/verifies supported versions |
| Local model bundle / local signal preview | owns | not authoritative execution state |
| Hard risk / OMS / QMT / broker | never | owns |
| Orders / fills / positions / ledger | never | owns |
| Lifecycle after `RESEARCH_PROMOTED` | never | owns `LEAN_VALIDATED`, `PAPER`, `PRODUCTION`, `RETIRED` |

P3 physically removed the legacy execution, hard-risk, broker/QMT, ledger, pretrade and shadow-execution implementations from this repository. The only order-like records produced here are simulated research/backtest records. They must never be treated as broker orders or broker-state writes.

The sole cross-repository handoff with execution semantics is a content-verified `target_portfolio_v1` / `TARGET_PORTFOLIO` artifact bound to exactly one `DataRelease`. Artifact v3 is current; v2 remains the explicitly supported previous contract rather than being reinterpreted in place.

See [Research Platform Epic #104 closeout](research_platform_epic104_closeout.md) for the audited capability matrix and the distinction between completed research software and external execution evidence.

## Research platform composition

The September 2026 platformization work composes existing subsystems rather than introducing a second runtime:

- provider registry + provider-neutral `DatasetRequest` / `CanonicalBatch` semantics;
- versioned `ResearchProfile` for instrument/calendar/label/asset assumptions;
- immutable release, feature, prediction and artifact identities;
- governed plan/matrix/resume research workflow;
- versioned Strategy SDK descriptors layered over the existing Qlib research-backtest implementations;
- one versioned target handoff to the execution plane.

These layers are independently versioned. Adding a provider, asset profile, strategy family or Artifact version must not silently redefine an existing ID.

## Release and data-source boundary

Both repositories may produce DataRelease artifacts, but governance capabilities remain release-bound.

A qlib-produced research release records its producer lineage. The execution platform may later verify or certify that immutable release; certification does not mutate the release identity.

Provider-neutral data semantics do not mean arbitrary provider substitution. Adapter-specific authentication, endpoint naming, pagination and retry remain inside the adapter. The research workflow consumes canonical batches with provenance, coverage, units, time semantics and auditable error classification. Switching source requires new lineage/release evidence; missing required data must not be silently filled from another provider.

Two lower-capability imports remain intentionally restricted:

- `ashare_qlib_import_v1` freezes an existing Qlib provider for exploratory local research. It cannot enter governed promotion/handoff merely because the data are readable.
- `ashare_market_import_v1` freezes OHLCV-oriented local market inputs. It requires bars, adjustment factors, a security master and a trading calendar and supports exploratory Alpha158 research, but governed handoff/promotion capabilities remain separately checked.

Capability checks are enforced at the relevant contract/handoff boundaries rather than inferred from directory names or provider availability.

## ResearchProfile boundary

Research profiles describe research semantics, not execution authorization. Current implemented profiles include A-share common equity and the first ETF slice. Every profile is constrained to `promotion_scope="research_only"` and to research/backtest/diagnostic workflows.

Future HK/US equities, FX, convertible bonds, futures or options require their own versioned profile and evidence. A symbol mapper, metadata record or broker template is not proof that an asset is certified for Paper/Live trading.

## Availability and integrity

Execution-platform availability is **fail-soft** for local research. DataRelease schema, identity, component and file verification remain **fail-closed**. When the execution platform is unavailable, already-verified local research can continue and governed bundles may remain in the durable outbox until a configured adapter can deliver and receive a successful acknowledgement.

OMS, broker, hard-risk, order, fill and ledger semantics never move into this repository during an outage or recovery.

## Production feedback boundary

Production feedback returns across a separate, non-execution boundary. This repository may consume immutable realized-label or aggregate evaluation inputs bound to one DataRelease and verify their identity/checksum before producing monitoring evidence.

It must not ingest or become the source of truth for mutable orders, fills, holdings, broker state or the execution ledger. `REALIZED_LABEL_SNAPSHOT` and `PREDICTION_EVALUATION_SNAPSHOT` are monitoring evidence only; they cannot select, promote, deploy or publish a model.

See [Production Feedback](production_feedback.md).

## Research-backtest strategy layer

Portfolio simulation inside Qlib is a research concern, not the Execution Plane. The Strategy SDK freezes two baseline families and one shadow-only family:

- `topk_dropout_v1` — frozen TopK/dropout baseline backed by `AShareTopkDropoutStrategy`;
- `rank_buffer_v1` — frozen rank-buffer baseline backed by `AShareRankBufferStrategy`;
- `score_threshold_shadow_v1` — evaluation-only overlay; no Qlib runtime class, no formal-candidate authority and no search authority.

`qlib_platform.backtesting.strategy_factory` continues to build the Qlib research strategy configuration. `strategy_sdk` adds explicit portfolio-weighting, rebalance, research-cost, evaluation and backend descriptors without changing the baseline signal semantics. `strategy_audit` and `execution_attribution` reconcile strategy intent against **simulated Qlib fills** and surface blocked/partial/action-mismatch and turnover/cost differences.

Uncalibrated research cost remains unavailable rather than being inferred from observed fills. Some historical/internal manifest fields use the namespace `execution.strategyPolicy`; that field name does not grant broker, OMS or authoritative execution ownership to this repository.

See [Portfolio Policy Layers](portfolio_v2_rank_buffer.md).

## Paper / Live boundary

The Research Platform Epic closes research software only. `paper`, `live` and `production` are not permitted qlib-platform deployment modes. They require independent execution-side evidence under `magic-alt/lean-local-platform#58` (or its governed successors) plus an explicit governance transition.

No research return, model count, provider count, Artifact version or Strategy SDK extension can bypass that boundary, authorize the final holdout, enable model selection, or grant publishing authority.
