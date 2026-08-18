from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from .execution_audit import reconciliation_manifest, reconcile_execution, require_reconciliation
from .signal_diagnostics import build_signal_diagnostics


def cost_stress_test(
    portfolio_report: pd.DataFrame,
    audit: pd.DataFrame,
    *,
    extra_bps: Iterable[float] = (0.0, 0.5, 1.0, 2.0, 5.0),
) -> pd.DataFrame:
    """Apply additional execution friction to the actual filled order values.

    This is an accounting stress test, not a fill-model replacement: it holds
    observed fills fixed and answers how much extra per-notional slippage the
    recorded strategy can absorb.
    """

    report = portfolio_report.copy()
    report.index = pd.to_datetime(report.index, errors="raise").normalize()
    report = report.loc[~report.index.duplicated(keep="last")].sort_index()
    if "account" not in report:
        raise ValueError("portfolio report missing account for cost stress")
    audit_frame = audit.copy()
    audit_frame["trade_date"] = pd.to_datetime(audit_frame["trade_date"], errors="raise").dt.normalize()
    order_value = pd.to_numeric(audit_frame.get("filled_value", 0.0), errors="coerce").fillna(0.0).abs()
    daily_value = order_value.groupby(audit_frame["trade_date"]).sum().reindex(report.index, fill_value=0.0)
    returns = pd.to_numeric(report.get("return", report["account"].pct_change()), errors="coerce").fillna(0.0)
    costs = pd.to_numeric(report.get("cost", 0.0), errors="coerce").fillna(0.0)
    bench = pd.to_numeric(report.get("bench", 0.0), errors="coerce").fillna(0.0)
    initial_account = float(report["account"].iloc[0] / max(1.0 + returns.iloc[0] - costs.iloc[0], 1e-12))
    rows: list[dict[str, float]] = []
    for bps in extra_bps:
        if bps < 0:
            raise ValueError("extra slippage bps must be non-negative")
        additional = daily_value * float(bps) / 10_000.0
        prior_account = report["account"].shift(1).fillna(initial_account).clip(lower=1e-12)
        stressed_daily = returns - costs - additional / prior_account
        terminal = initial_account * float((1.0 + stressed_daily).prod())
        benchmark_terminal = float((1.0 + bench).prod())
        rows.append(
            {
                "extra_slippage_bps": float(bps),
                "additional_cost": float(additional.sum()),
                "net_return": terminal / initial_account - 1.0,
                "benchmark_return": benchmark_terminal - 1.0,
                "net_excess_return": terminal / initial_account - benchmark_terminal,
            }
        )
    return pd.DataFrame(rows)


def write_p0_artifacts(
    run_dir: str | Path,
    *,
    strict_reconciliation: bool = True,
) -> Mapping[str, Any]:
    """Persist the P0 baseline evidence alongside an existing research run."""

    root = Path(run_dir).expanduser().resolve()
    report = pd.read_parquet(root / "portfolio_report.parquet")
    audit = pd.read_parquet(root / "strategy_audit.parquet")
    reconciliation, result = reconcile_execution(audit, report)
    reconciliation_path = root / "audit_reconciliation.parquet"
    reconciliation.to_parquet(reconciliation_path, index=False)
    stress = cost_stress_test(report, audit)
    stress_path = root / "cost_stress.parquet"
    stress.to_parquet(stress_path, index=False)
    payload: dict[str, Any] = {
        "auditReconciliation": reconciliation_manifest(result),
        "costStress": {
            "artifact": stress_path.name,
            "extraSlippageBps": stress["extra_slippage_bps"].tolist(),
        },
    }
    prediction_path = root / "oos_predictions.parquet"
    label_path = root / "oos_labels.parquet"
    if prediction_path.is_file() and label_path.is_file():
        daily, summary = build_signal_diagnostics(
            pd.read_parquet(prediction_path), pd.read_parquet(label_path)
        )
        diagnostics_path = root / "signal_diagnostics.parquet"
        daily.to_parquet(diagnostics_path, index=False)
        summary_path = root / "signal_diagnostics_summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["signalDiagnostics"] = {"artifact": diagnostics_path.name, **summary}
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    manifest.update(payload)
    artifacts = manifest.setdefault("artifacts", [])
    names = {str(item.get("name")) for item in artifacts if isinstance(item, Mapping)}
    for path in (reconciliation_path, stress_path):
        if path.name not in names:
            artifacts.append({"name": path.name, "localPath": str(path)})
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if strict_reconciliation:
        require_reconciliation(result)
    return payload
