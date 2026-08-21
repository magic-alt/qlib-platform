# Portfolio V2 — Rank Buffer execution layer

The 2×2 TopkDropout sweep (`Hold × Drop`) is closed. Its core finding is that
`Hold=1` and `Drop=5` were not independent alpha sources but two ways to solve
the same problem: **portfolio refresh speed**. Portfolio V2 therefore stops
parameter-hunting `TopkDropout` and wires the existing-but-dangling
`RankBufferPolicy` into the formal Qlib backtest, audit, lineage and gate chain.

## Fixed benchmark lines

| Role                          | Strategy          | Policy id          |
| ----------------------------- | ----------------- | ------------------ |
| Sticky baseline               | Top10 / Drop3 / H5 | `topk_dropout_v1`  |
| Fast efficient baseline       | Top10 / Drop3 / H1 | `topk_dropout_v1`  |
| Fast aggressive ceiling       | Top10 / Drop5 / H1 | `topk_dropout_v1`  |
| New candidate                 | RB10/20/R3/H1     | `rank_buffer_v1`   |

C is a low-turnover control, not a primary candidate.

## First candidate (pre-registered, frozen)

`configs/portfolio/rank_buffer_alpha158_v1.yaml`

```text
target_size        10
entry_rank         10
exit_rank          20
max_replacements    3
hold_thresh         1
risk_degree         0.95
```

The Phase 2 research asset `configs/portfolio/rank_buffer_phase2_v1.yaml` is
**not modified**; this new file is a separate lineage.

## What was wired up

- `RankBufferPolicy.target_size` decouples the desired holding count from the
  entry-rank eligibility (previously `slots = entry_rank - retained` conflated
  the two).
- `src/tushare_qlib/qlib_strategies.py::RankBufferStrategy(BaseSignalStrategy)`
  implements the decision in Qlib's execution semantics: sell exit-rank
  breaches (worst first, capped by `max_replacements`), refill open slots up to
  `target_size` from names within `entry_rank` — fast escape, slow churn.
- `src/tushare_qlib/strategy_factory.py` resolves the policy
  (`topk_dropout_v1` | `rank_buffer_v1`) and builds the Qlib `PortAnaRecord`
  strategy block, so `train_backtest_select` and `backtest_predictions` share
  one path instead of hard-coding `TopkDropoutStrategy`.
- `strategy_audit.build_strategy_audit` replays the same decision function
  (`topk_dropout_decision` or `rank_buffer_decision`) and attaches Qlib's
  actual fills; rank buffer rows carry `EXIT_RANK_BREACH`, `ENTRY_RANK`,
  `INSIDE_HOLD_BUFFER`, `MAX_REPLACEMENTS`, `HOLD_THRESHOLD`,
  `NOT_TRADABLE_SELL` / `NOT_TRADABLE_BUY`.
- `ResearchExperimentSpec` accepts `rank_buffer_v1` and fingerprints the
  **resolved** policy, keeping the historical topk hash identical.
- Manifests record `execution.strategyPolicy` plus the policy-specific block
  (`topkDropout` or `rankBuffer`), and `signal_scores_*.parquet` carries the
  strategy columns for the active policy.

## Evaluation objective

Stop maximizing raw return; score candidates on

```text
Net Excess + ExcessIR + MDD + Turnover + CostStress
```

with a true per-notional slippage stress (`filled_value * bps / 10000`,
`extra_bps = (0, 1, 2, 3, 5, 10)`) instead of multiplying the explicit cost by
a factor. Benchmark diagnostics (beta / tracking error / up-down capture /
rolling 63D beta and excess) are produced by the attribution study so a
candidate is also judged on whether it participated in the CSI300 rally.

## Research gate

The `RankICIR >= 0.50` / `ICIR >= 0.50` / `ExcessIR >= 0.50` thresholds stay
**frozen**. Rank buffer fixes signal→PnL conversion, not signal stability; the
remaining benchmark gap is a signal problem and must not be hidden by loosening
the gate.

## Next stages (not yet implemented)

1. Wire the existing `PortfolioPolicy` (`max_position`, `max_turnover`) into
   the formal chain as a second portfolio layer.
2. Benchmark-relative constraints and, later, a true CSI300 enhanced-index
   optimizer.

## Benchmark diagnostics (implemented)

`portfolio_attribution.py` now derives the Portfolio V2.3 benchmark
diagnostics directly from the existing daily gross/net/benchmark bridge:

- `derive_benchmark_diagnostics` — per-scope `portfolio_beta` (vs CSI300),
  `tracking_error` (annualized net-excess vol), `up_capture` / `down_capture`
  (gross vs benchmark on up/down days), `gross_active_return`,
  `net_active_return`.
- `derive_rolling_benchmark_diagnostics` — daily `rolling_beta` and
  `rolling_excess_return` over a 63-session window (configurable), so beta
  drift and alpha decay phases are visible without a risk model.

`attribution_study` publishes both as `benchmark_diagnostics.parquet` and
`rolling_benchmark_diagnostics.parquet` alongside the existing attribution
frames. These answer whether the Rank Buffer candidate participated in the
CSI300 rally rather than just reporting raw return.
