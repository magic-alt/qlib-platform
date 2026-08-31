---
status: ACTIVE
owner: architecture
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-28
---

# Architecture Boundary

`qlib-platform` is an autonomous Research / Alpha Factory with its own lifecycle.
It consumes or publishes an immutable `DataRelease`, builds features and models,
performs walk-forward research, and publishes only research artifacts through
Artifact Contract v2. `platform` is an optional Execution Plane, not a startup,
authentication, or research dependency.

Owned here:

- Qlib materialization, feature store, factors, training and tuning;
- local authentication and RBAC for multi-user API/UI deployments;
- local/TuShare market-data bootstrap and immutable research DataRelease publication;
- IC, RankIC, stability, walk-forward and research portfolio analysis;
- `MODEL_RELEASE`, `STRATEGY_POLICY`, `SIGNAL_SNAPSHOT`,
  `TARGET_PORTFOLIO`, and `VALIDATION_RESULT`;
- promotion no further than `RESEARCH_PROMOTED`.

Owned by `platform`:

- optional canonical-data publication and execution-authoritative DataRelease certification;
- authoritative LEAN backtests and execution semantics;
- hard risk, paper/shadow trading, OMS, broker/QMT, orders, fills and ledger;
- `LEAN_VALIDATED`, `PAPER`, `PRODUCTION`, and `RETIRED` transitions.

P3 has physically removed the legacy execution, hard-risk, broker/QMT, ledger,
pretrade and shadow implementations from this repository. The QMT query gateway
now lives in `platform`; the only Qlib code paths in this repository are
research backtests that emit simulated orders inside the Qlib exchange — never
broker orders or broker-state writes. The sole integration boundary is a
content-addressed `TARGET_PORTFOLIO` within an Artifact Contract v2 bundle
bound to one `DataRelease`.

Both repositories may produce a DataRelease v2. A qlib-produced release has a
`lineage.producer` of `qlib-platform` and a research governance level. An imported
legacy Qlib provider is frozen as `ashare_qlib_import_v1` and remains exploratory:
it can be used for interactive local research but cannot enter Phase 2/Phase 3 or
Artifact Contract v2 export. Platform may later verify or certify a research release;
that certification does not change the release identity.

An OHLCV-oriented local import is frozen as `ashare_market_import_v1`. It requires
bars, adjustment factors, a security master, and a trading calendar, and can be
materialized into a Qlib DatasetVersion for exploratory Alpha158 training/backtests.
It cannot enter Phase 2/Phase 3, research promotion, TARGET_PORTFOLIO, LEAN handoff, or
Artifact Contract v2 export. Those restrictions are manifest capabilities enforced at
every handoff command.

Platform availability is fail-soft. DataRelease schema, identity, component and file
verification remain fail-closed. When platform is unavailable, research continues and
verified Artifact v2 bundles remain in the local durable outbox until an adapter can
deliver and acknowledge them. OMS, broker, hard-risk, order, fill, and ledger semantics
never enter this repository.

Production feedback returns across a separate, non-execution boundary. This repository may
consume immutable realized-label or aggregate execution-evaluation artifacts that are bound to
one DataRelease and verify their identity/checksum before producing monitoring evidence. It must
not ingest or become the source of truth for mutable orders, fills, holdings, broker state or the
execution ledger. Feedback evidence cannot itself promote, deploy or publish a model.

Production feedback returns across a separate, non-execution boundary. This repository may
consume immutable realized-label or aggregate execution-evaluation artifacts that are bound to
one DataRelease and verify their identity/checksum before producing monitoring evidence. It must
not ingest or become the source of truth for mutable orders, fills, holdings, broker state or the
execution ledger. Feedback evidence cannot itself promote, deploy or publish a model.

## Strategy policy layer

Portfolio construction is a first-class, policy-typed research stage. The
canonical config selects exactly one execution policy:

- `topk_dropout_v1` — Qlib-native `TopkDropoutStrategy`
  (`qlib.contrib.strategy.signal_strategy`), the frozen research default.
- `rank_buffer_v1` — `RankBufferStrategy`
  (`tushare_qlib.qlib_strategies`), a pre-registered buy/hold rank buffer with
  `target_size` decoupled from `entry_rank`.

`tushare_qlib.strategy_factory` resolves the policy, builds the Qlib
`PortAnaRecord` strategy block, and tags manifests with `strategyPolicy`.
`strategy_audit.build_strategy_audit` replays the same decision function
(`topk_dropout_decision` or `rank_buffer_decision`) and reconciles it against
Qlib's actual fills, so the backtest, the decision replay and the audit always
share one implementation. The rank buffer first candidate is pre-registered in
`configs/portfolio/rank_buffer_alpha158_v1.yaml`.
