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

P3 has physically removed the legacy execution, hard-risk, broker/QMT, ledger,
pretrade and shadow implementations from this repository. The QMT query gateway
now lives in `platform`; the only Qlib code paths in this repository are
research backtests that emit simulated orders inside the Qlib exchange — never
broker orders or broker-state writes. The sole integration boundary is a
content-addressed `TARGET_PORTFOLIO` within an Artifact Contract v2 bundle
bound to one `DataRelease`.

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
