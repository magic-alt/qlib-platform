---
status: HISTORICAL
owner: research
applies_to_commit: 4f5c5d5
last_verified: 2026-08-28
superseded_by: architecture_boundary.md
---

# P0 research baseline

> HISTORICAL. Order-like fields below are research simulation/audit representations. They are not the
> current qlib-to-platform handoff contract.

The P0 baseline is research-only. It must not be passed to LEAN or QMT until
all execution reconciliation checks pass.

For a completed run, generate the required evidence with the repository-local
interpreter:

```powershell
$RepoPython = '.\.venv\Scripts\python.exe'
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

`latest_strategy_targets` historically exposed `modelTopkCandidates`,
`strategyTargetPositions`, `nextTradeOrders`, and `expectedPostTradePositions` for research
simulation and audit. None is an execution-adapter interface. The current cross-repository handoff is
only a content-addressed `TARGET_PORTFOLIO` bound to one DataRelease through Artifact Contract v2.

The registered orthogonal experiment design is
`configs/research/p0_strategy_execution_matrix.yaml`. Run portfolio policies on
the same immutable prediction snapshot first, then compare close and open labels
under the same policy, and only then compare frozen and walk-forward retraining.

After all child runs have passed their individual audits, write the checksum-backed
orthogonal synthesis receipt:

```powershell
& $RepoPython scripts/synthesize_p0_orthogonal_audit.py `
  --child-run-dir data/output/research/<CHILD_RUN_1> `
  --child-run-dir data/output/research/<CHILD_RUN_2> `
  --output data/output/research/p0_orthogonal_audit_receipt.json
```

The synthesis fails closed if a child audit is missing, failed, tampered with, or
inconsistent with the other child runs.
