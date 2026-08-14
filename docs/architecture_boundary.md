# Architecture Boundary

`qlib-platform` is the Research / Alpha Factory. It consumes an immutable
`DataRelease`, builds features and models, performs walk-forward research, and
publishes only research artifacts through Artifact Contract v2.

Owned here:

- Qlib materialization, feature store, factors, training and tuning;
- IC, RankIC, stability, walk-forward and research portfolio analysis;
- `MODEL_RELEASE`, `STRATEGY_POLICY`, `SIGNAL_SNAPSHOT`,
  `TARGET_PORTFOLIO`, and `VALIDATION_RESULT`;
- promotion no further than `RESEARCH_PROMOTED`.

Owned by `platform`:

- TuShare production ingestion and canonical DataRelease publication;
- authoritative LEAN backtests and execution semantics;
- hard risk, paper/shadow trading, OMS, broker/QMT, orders, fills and ledger;
- `LEAN_VALIDATED`, `PAPER`, `PRODUCTION`, and `RETIRED` transitions.

The existing execution, broker, ledger and pretrade modules are frozen legacy
surface pending P3 migration. New research code must not import or extend them.
The sole integration boundary is a content-addressed `TARGET_PORTFOLIO` within
an Artifact Contract v2 bundle bound to one `DataRelease`.
