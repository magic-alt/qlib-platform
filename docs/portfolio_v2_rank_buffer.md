---
status: ACTIVE
owner: research
applies_to_commit: 4f3f4369b6e55186967bc726bb8dd87fff0e5d70
last_verified: 2026-08-31
---

# Portfolio V2 — Rank Buffer research-backtest policy

The 2×2 TopkDropout sweep (`Hold × Drop`) is closed. Its core finding is that `Hold=1` and `Drop=5` were not independent alpha sources but two ways to solve the same problem: **portfolio refresh speed**. Portfolio V2 therefore stops parameter-hunting `TopkDropout` and wires the existing `RankBufferPolicy` into the formal Qlib research-backtest, audit, lineage and gate chain.

> **Terminology:** TopkDropout and RankBuffer run inside Qlib's simulated exchange. They are research-backtest strategy policies, not the repository's Execution Plane and not broker/QMT execution policies. Some manifest fields retain the historical namespace `execution.strategyPolicy`; that field name does not transfer execution ownership.

## Fixed benchmark lines

| Role | Strategy | Policy id |
| --- | --- | --- |
| Sticky baseline | Top10 / Drop3 / H5 | `topk_dropout_v1` |
| Fast efficient baseline | Top10 / Drop3 / H1 | `topk_dropout_v1` |
| Fast aggressive ceiling | Top10 / Drop5 / H1 | `topk_dropout_v1` |
| New candidate | RB10/20/R3/H1 | `rank_buffer_v1` |

C is a low-turnover control, not a primary candidate.

## First candidate (pre-registered, frozen)

`configs/portfolio/rank_buffer_alpha158_v1.yaml`

```text
target_size         10
entry_rank          10
exit_rank           20
max_replacements     3
hold_thresh          1
risk_degree          0.95
```

The Phase 2 research asset `configs/portfolio/rank_buffer_phase2_v1.yaml` is not modified; this file is a separate lineage.

## What is wired up

- `RankBufferPolicy.target_size` decouples desired holding count from entry-rank eligibility.
- `RankBufferStrategy(BaseSignalStrategy)` implements simulated Qlib decisions: sell exit-rank breaches (worst first, capped by `max_replacements`) and refill open slots up to `target_size` from names within `entry_rank`.
- `strategy_factory.py` resolves `topk_dropout_v1 | rank_buffer_v1` and builds the Qlib `PortAnaRecord` strategy block so training/backtest paths share one policy implementation.
- `strategy_audit.build_strategy_audit` replays the same decision function and reconciles it against Qlib's simulated fills. Rank-buffer audit reasons include `EXIT_RANK_BREACH`, `ENTRY_RANK`, `INSIDE_HOLD_BUFFER`, `MAX_REPLACEMENTS`, `HOLD_THRESHOLD`, `NOT_TRADABLE_SELL` and `NOT_TRADABLE_BUY`.
- `ResearchExperimentSpec` fingerprints the resolved policy while preserving historical Topk identity behavior.
- Research manifests record `execution.strategyPolicy` plus the policy-specific block (`topkDropout` or `rankBuffer`), and score artifacts carry strategy-specific audit columns.

## Evaluation objective

Stop maximizing raw return; score candidates on:

```text
Net Excess + ExcessIR + MDD + Turnover + CostStress
```

Cost stress uses additional slippage per filled notional (`filled_value * bps / 10000`, `extra_bps = (0, 1, 2, 3, 5, 10)`) rather than multiplying the explicit transaction cost by an arbitrary factor.

Benchmark diagnostics include beta, tracking error, up/down capture and rolling 63-session beta/excess so a candidate is evaluated against its CSI300 participation rather than raw return alone.

## Research gate

The frozen boolean gate remains:

```text
(ICIR >= 0.50 OR RankICIR >= 0.50)
AND ExcessIR >= 0.50
AND all other research, portfolio and lineage conditions
```

Rank Buffer addresses signal-to-research-PnL conversion. It does not repair unstable signal quality, and the gate must not be loosened to hide that distinction.

## Two portfolio layers

These layers are separate:

1. **Qlib research-backtest strategy policy** — `TopkDropout` or `RankBuffer`; stateful retain/sell/replace simulation used to evaluate research PnL and audit decisions.
2. **PortfolioPolicy** — implemented MODEL_TOPK-to-`TARGET_PORTFOLIO` target-weight layer; applies weighting, `max_position`, `max_exposure`, `max_group_exposure` and `max_turnover` before Artifact Contract v2 handoff.

`PortfolioPolicy` is not the RankBuffer simulation policy. Future work may add benchmark-relative constraints and a true CSI300 enhanced-index optimizer without changing this ownership boundary.

## Benchmark diagnostics

`portfolio_attribution.py` derives:

- `derive_benchmark_diagnostics` — portfolio beta vs CSI300, annualized tracking error, up/down capture, gross/net active return;
- `derive_rolling_benchmark_diagnostics` — rolling beta and rolling excess return over a configurable 63-session window.

The attribution study publishes these as `benchmark_diagnostics.parquet` and `rolling_benchmark_diagnostics.parquet` alongside existing attribution frames.
