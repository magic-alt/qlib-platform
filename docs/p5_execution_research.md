---
status: ACTIVE
owner: research-platform
last_verified: 2026-09-06
---

# P5-C — Institutional Execution Research

P5-C adds an institutional **execution-research** surface to `qlib-platform`. It does not turn the
research repository into an OMS, broker gateway or authoritative live-execution engine.

## Responsibility boundary

The package lives under `qlib_platform.research.execution` and consumes research parent orders plus
intraday market-data snapshots. Its outputs are schedules, deterministic research fills and
execution diagnostics. They are evidence for research and model comparison, not broker commands.

The following remain outside this repository:

- live order submission, cancellation and replacement;
- broker session state and order-state machines;
- authoritative execution ledgers;
- live pre-trade / post-trade hard-risk enforcement;
- authoritative LEAN or broker execution semantics.

Those responsibilities remain in the separate execution platform.

## Intraday data contract

`normalize_intraday_bars` requires:

- `timestamp`;
- `instrument`;
- positive `close`;
- non-negative `volume`.

Optional research fields include bid/ask quotes, spread bps, queue-ahead quantity, market-open state
and rejection state. The contract fails closed on invalid timestamps, duplicate
`(timestamp, instrument)` rows, invalid quotes, negative volume or ambiguous one-sided quotes.

The benchmark surface records:

- arrival price;
- realized-volume VWAP;
- time-average price (TWAP benchmark);
- end-of-window price;
- total market volume.

## Schedule research

P5-C provides deterministic integer schedules:

- **TWAP** — equal parent quantity across available bars with deterministic integer remainder
  allocation;
- **VWAP** — ex-post allocation against realized intraday volume;
- **POV** — quantity capped by an explicit participation rate per bar.

The VWAP schedule is deliberately labeled an **ex-post research benchmark**. It is not a live volume
forecast and must not be interpreted as a causal production execution policy when realized future
volume is used.

TWAP/VWAP conserve parent quantity exactly. POV may leave an explicit unscheduled remainder when
market capacity is insufficient.

## Fill, queue, spread, impact and latency model

`simulate_schedule` is deterministic. It computes research expected fills from:

- a maximum participation-rate capacity;
- queue-ahead quantity and queue-access fraction;
- child cancellation or rejection state;
- bid/ask spread or a default spread assumption;
- square-root participation impact;
- latency penalty;
- fee assumptions.

The resulting fill evidence includes target and filled quantity, partial-fill status, expected fill
probability, reference/fill prices, capacity, participation, queue, spread, impact, latency,
slippage and fees.

This expected-fill model is intentionally reproducible. It does not claim to reproduce exchange
matching-engine queue priority or hidden-liquidity behavior.

## Implementation shortfall

`implementation_shortfall` decomposes signed execution cost into:

1. **delay cost** — decision price to arrival price over the parent quantity;
2. **execution cost** — arrival price to realized average fill over filled quantity;
3. **opportunity cost** — arrival price to end-of-window price over unfilled quantity;
4. **fees**.

The components reconcile to total implementation shortfall. BUY and SELL orders share one explicit
direction convention, and results include arrival/VWAP slippage plus total shortfall in basis points.

## Broker-event research

`analyze_broker_events` summarizes externally supplied event snapshots without mutating broker state.
It can report:

- requested/filled quantity and fill ratio;
- rejects, cancellations and partial fills;
- acknowledgement latency p50/p95;
- signed realized slippage when side/reference/fill prices are present.

## Accounting reconciliation

P5-C does not duplicate portfolio accounting. `execution_audit_from_fills` converts research fills to
the existing `backtesting.execution_audit` schema, and `reconcile_simulated_execution` delegates to
the existing certified turnover/cost/position reconciliation.

The adapter fails closed on negative inventory, invalid fill prices, invalid timestamps and malformed
quantities.

## Certification contract

`.github/workflows/p5-execution-research.yml` runs:

- P5-C execution tests;
- P5-B portfolio-construction regressions;
- P5-A risk regressions;
- the existing portfolio optimizer/risk-model regressions;
- Ruff lint and format checks;
- mypy for the execution-research package and certified execution-audit bridge.

P5-C is not complete until its dedicated contract and the repository-wide CI/security/docs/release
checks are green on one immutable PR head.

## Governance invariants

P5-C does not:

- open or consume the sealed final holdout;
- create/select/promote formal model candidates;
- authorize research publishing;
- change PIT/fold/OOS governance;
- modify the DataRelease-bound `TARGET_PORTFOLIO` cross-repository handoff;
- acquire OMS or broker authority.

The active research program therefore remains Phase 3-D diagnosis-only while infrastructure work
advances through P5-C.
