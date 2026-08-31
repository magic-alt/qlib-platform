---
status: ACTIVE
owner: architecture
applies_to_commit: 4f3f4369b6e55186967bc726bb8dd87fff0e5d70
last_verified: 2026-08-31
---

# Architecture Boundary

`qlib-platform` is an autonomous Research / Alpha Factory with its own lifecycle. It consumes or publishes an immutable `DataRelease`, builds features and models, performs walk-forward research, and publishes only governed research artifacts through Artifact Contract v2. `platform` is an optional Execution Plane, not a startup, authentication, or local-research dependency.

## Ownership matrix

| Concern | qlib-platform | platform |
| --- | --- | --- |
| Local/TuShare research data bootstrap | owns | no |
| DataRelease publication | research-governed releases | may publish/certify execution-authoritative releases |
| Qlib DatasetVersion materialization | owns | no |
| PIT features, factors, model training/tuning | owns | no |
| IC/RankIC, walk-forward, research backtests | owns | may independently perform authoritative LEAN validation |
| Research portfolio simulation | owns, inside Qlib only | no |
| `TARGET_PORTFOLIO` construction | owns | consumes/verifies |
| Artifact Contract v2 | exports | ingests/verifies |
| Local model bundle / local signal preview | owns | not authoritative execution state |
| Hard risk / OMS / QMT / broker | never | owns |
| Orders / fills / positions / ledger | never | owns |
| Lifecycle after `RESEARCH_PROMOTED` | never | owns `LEAN_VALIDATED`, `PAPER`, `PRODUCTION`, `RETIRED` |

P3 physically removed the legacy execution, hard-risk, broker/QMT, ledger, pretrade and shadow implementations from this repository. The QMT query gateway now lives in `platform`; the only order-like records produced here are simulated Qlib backtest records. They must never be treated as broker orders or broker-state writes.

The sole cross-repository handoff with execution semantics is a content-verified `TARGET_PORTFOLIO` inside an Artifact Contract v2 bundle bound to exactly one `DataRelease`.

## Release boundary

Both repositories may produce DataRelease v2 artifacts, but governance capabilities remain release-bound.

A qlib-produced research release records `lineage.producer=qlib-platform`. Platform may later verify or certify that immutable release; certification does not mutate the release identity.

Two lower-capability imports are intentionally restricted:

- `ashare_qlib_import_v1` freezes an existing Qlib provider for exploratory local research. It cannot enter Phase 2/Phase 3, research promotion, `TARGET_PORTFOLIO`, LEAN handoff or Artifact Contract v2 export.
- `ashare_market_import_v1` freezes OHLCV-oriented local market inputs. It requires bars, adjustment factors, a security master and a trading calendar and supports exploratory Alpha158 research, but the same governed handoff/promotion capabilities remain disabled.

Capability checks are enforced at handoff commands rather than inferred from directory names.

## Availability and integrity

Platform availability is **fail-soft** for local research. DataRelease schema, identity, component and file verification remain **fail-closed**. When platform is unavailable, already-verified local research can continue and Artifact v2 bundles remain in the durable outbox until a configured adapter can deliver and receive a successful acknowledgement.

OMS, broker, hard-risk, order, fill and ledger semantics never move into this repository during an outage or recovery.

## Production feedback boundary

Production feedback returns across a separate, non-execution boundary. This repository may consume immutable realized-label or aggregate evaluation inputs bound to one DataRelease and verify their identity/checksum before producing monitoring evidence.

It must not ingest or become the source of truth for mutable orders, fills, holdings, broker state or the execution ledger. `REALIZED_LABEL_SNAPSHOT` and `PREDICTION_EVALUATION_SNAPSHOT` are monitoring evidence only; they cannot select, promote, deploy or publish a model and are not Artifact Contract v2 graph nodes.

See [Production Feedback](production_feedback.md).

## Research-backtest strategy layer

Portfolio simulation inside Qlib is a research concern, not the Execution Plane. The configured strategy policy selects one research-backtest implementation:

- `topk_dropout_v1` — Qlib-native `TopkDropoutStrategy` (`qlib.contrib.strategy.signal_strategy`), the frozen research default;
- `rank_buffer_v1` — `RankBufferStrategy` (`tushare_qlib.qlib_strategies`), a pre-registered buy/hold rank-buffer strategy with `target_size` decoupled from `entry_rank`.

`tushare_qlib.strategy_factory` resolves the policy, builds the Qlib `PortAnaRecord` strategy block, and tags research manifests with `strategyPolicy`. `strategy_audit.build_strategy_audit` replays the same decision function and reconciles it against **simulated Qlib fills**.

Some historical/internal manifest fields use the namespace `execution.strategyPolicy`; that field name does not grant broker, OMS or authoritative execution ownership to this repository.

The rank-buffer candidate is pre-registered in `configs/portfolio/rank_buffer_alpha158_v1.yaml`. See [Portfolio Policy Layers](portfolio_v2_rank_buffer.md).
