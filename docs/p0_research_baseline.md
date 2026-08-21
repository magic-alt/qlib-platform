# P0 research baseline

The P0 baseline is research-only. It must not be passed to LEAN or QMT until
all execution reconciliation checks pass.

For a completed run, generate the required evidence with the repository-local
interpreter:

```powershell
$RepoPython = '.\.venv\python.exe'
& $RepoPython scripts/generate_p0_baseline_artifacts.py data/output/research/<RUN_ID>
```

The command writes `audit_reconciliation.parquet`, `signal_diagnostics.parquet`
(when OOS predictions and labels are present), and `cost_stress.parquet`. It
fails with `AUDIT_RECONCILIATION_FAILED` if any of these invariants fails:

- executed notional equals cumulative portfolio turnover by session;
- executed trade cost equals cumulative portfolio cost by session;
- `quantity_before + signed_filled_quantity = quantity_after` for every audited position.

`--allow-reconciliation-failure` exists only to inspect old, incomplete qrun
exports. Such a run remains ineligible for production research promotion.

`latest_strategy_targets` exposes four separate payloads for a signal date:
`modelTopkCandidates`, `strategyTargetPositions`, `nextTradeOrders`, and
`expectedPostTradePositions`. Execution adapters must consume only orders and
post-trade positions, never raw model candidates.

The registered orthogonal experiment design is
`configs/research/p0_strategy_execution_matrix.yaml`. Run portfolio policies on
the same immutable prediction snapshot first, then compare close and open labels
under the same policy, and only then compare frozen and walk-forward retraining.
