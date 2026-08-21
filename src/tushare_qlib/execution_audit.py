from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd


_TOLERANCE = 1e-6


@dataclass(frozen=True)
class ReconciliationResult:
    passed: bool
    turnover_match: bool
    cost_match: bool
    position_match: bool
    filled_turnover: float
    portfolio_turnover: float
    filled_cost: float
    portfolio_cost: float
    max_position_residual: float
    sessions: int

    def to_manifest(self) -> dict[str, object]:
        return asdict(self)


def _numeric(frame: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    if column not in frame:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").fillna(default)


def _cumulative_deltas(report: pd.DataFrame, column: str) -> pd.Series:
    if column not in report:
        return pd.Series(dtype=float)
    cumulative = pd.to_numeric(report[column], errors="coerce").dropna()
    if cumulative.empty:
        return pd.Series(dtype=float)
    cumulative.index = pd.to_datetime(cumulative.index).normalize()
    return cumulative.diff().fillna(cumulative.iloc[0]).clip(lower=0.0)


def reconcile_execution(
    audit: pd.DataFrame,
    portfolio_report: pd.DataFrame,
    *,
    tolerance: float = _TOLERANCE,
) -> tuple[pd.DataFrame, ReconciliationResult]:
    """Reconcile executed orders against Qlib portfolio accounting.

    The order audit is the authoritative source for signed inventory changes;
    Qlib's cumulative turnover and cost columns are the independent accounting
    source.  Keeping both sources separate makes a missing BUY or SELL record a
    hard, machine-detectable failure rather than a presentation defect.
    """

    if tolerance < 0:
        raise ValueError("reconciliation tolerance must be non-negative")
    required = {"trade_date", "actual_action", "filled_quantity", "filled_value", "trade_cost"}
    missing = required - set(audit.columns)
    if missing:
        raise ValueError(f"strategy audit missing reconciliation columns: {sorted(missing)}")
    if portfolio_report.empty:
        raise ValueError("portfolio report is required for execution reconciliation")

    orders = audit.copy()
    orders["trade_date"] = pd.to_datetime(orders["trade_date"], errors="raise").dt.normalize()
    orders["filled_value"] = _numeric(orders, "filled_value").abs()
    orders["trade_cost"] = _numeric(orders, "trade_cost").abs()
    direction = orders["actual_action"].astype(str).map({"BUY": 1.0, "SELL": -1.0}).fillna(0.0)
    orders["signed_filled_quantity"] = _numeric(orders, "filled_quantity").abs() * direction
    executed = orders.loc[direction.ne(0.0)].copy()
    filled = executed.groupby("trade_date", sort=True).agg(
        filled_turnover=("filled_value", "sum"),
        filled_cost=("trade_cost", "sum"),
        filled_orders=("actual_action", "size"),
    )

    report = portfolio_report.copy()
    report.index = pd.to_datetime(report.index, errors="raise").normalize()
    report = report.loc[~report.index.duplicated(keep="last")].sort_index()
    accounting = pd.DataFrame(index=report.index)
    accounting["portfolio_turnover"] = (
        _cumulative_deltas(report, "total_turnover").reindex(report.index).fillna(0.0)
    )
    accounting["portfolio_cost"] = _cumulative_deltas(report, "total_cost").reindex(report.index).fillna(0.0)

    position = pd.DataFrame(index=pd.DatetimeIndex(sorted(set(orders["trade_date"]))), dtype=float)
    if {"quantity_before", "quantity_after"}.issubset(orders.columns):
        orders["quantity_before"] = _numeric(orders, "quantity_before")
        orders["quantity_after"] = _numeric(orders, "quantity_after")
        orders["position_residual"] = (
            orders["quantity_before"] + orders["signed_filled_quantity"] - orders["quantity_after"]
        )
        position = orders.groupby("trade_date", sort=True).agg(
            max_position_residual=("position_residual", lambda values: float(np.abs(values).max())),
            position_rows=("position_residual", "size"),
        )
    else:
        position["max_position_residual"] = np.inf
        position["position_rows"] = 0

    daily = accounting.join(filled, how="outer").join(position, how="outer").fillna(0.0)
    daily.index.name = "trade_date"
    daily["turnover_residual"] = daily["filled_turnover"] - daily["portfolio_turnover"]
    daily["cost_residual"] = daily["filled_cost"] - daily["portfolio_cost"]
    daily["turnover_match"] = daily["turnover_residual"].abs().le(tolerance)
    daily["cost_match"] = daily["cost_residual"].abs().le(tolerance)
    daily["position_match"] = daily["max_position_residual"].abs().le(tolerance)
    daily["passed"] = daily[["turnover_match", "cost_match", "position_match"]].all(axis=1)
    result = ReconciliationResult(
        passed=bool(daily["passed"].all()) if len(daily) else False,
        turnover_match=bool(daily["turnover_match"].all()) if len(daily) else False,
        cost_match=bool(daily["cost_match"].all()) if len(daily) else False,
        position_match=bool(daily["position_match"].all()) if len(daily) else False,
        filled_turnover=float(daily["filled_turnover"].sum()),
        portfolio_turnover=float(daily["portfolio_turnover"].sum()),
        filled_cost=float(daily["filled_cost"].sum()),
        portfolio_cost=float(daily["portfolio_cost"].sum()),
        max_position_residual=float(daily["max_position_residual"].abs().max())
        if len(daily)
        else float("inf"),
        sessions=int(len(daily)),
    )
    return daily.reset_index(), result


def require_reconciliation(result: ReconciliationResult) -> None:
    if result.passed:
        return
    raise RuntimeError(
        "AUDIT_RECONCILIATION_FAILED: "
        f"turnover={result.turnover_match}, cost={result.cost_match}, position={result.position_match}"
    )


def reconciliation_manifest(result: ReconciliationResult) -> Mapping[str, Any]:
    return result.to_manifest()
