from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from qlib_platform.backtesting.strategy_sdk import ResearchCostModel


@dataclass(frozen=True)
class StrategyExecutionAttribution:
    rows: int
    matched: int
    blocked: int
    partial: int
    action_mismatch: int
    requested_quantity: float
    filled_quantity: float
    turnover_drift_ratio: float | None
    actual_cost: float
    expected_cost: float | None
    cost_drift: float | None
    cost_model_status: str

    def to_manifest(self) -> dict[str, object]:
        return asdict(self)


def attribute_strategy_execution(
    audit: pd.DataFrame,
    *,
    cost_model: ResearchCostModel | None = None,
) -> pd.DataFrame:
    """Attribute baseline decision intent to observed execution differences.

    Turnover drift uses requested-vs-filled quantity because StrategyAudit has
    no trustworthy requested notional before an execution price exists.  Cost
    drift is emitted only when an explicit research cost model is calibrated.
    """

    required = {
        "target_action",
        "actual_action",
        "order_requested",
        "requested_quantity",
        "filled_quantity",
        "filled_value",
        "trade_cost",
        "execution_status",
    }
    missing = required - set(audit.columns)
    if missing:
        raise ValueError(f"strategy audit missing columns: {sorted(missing)}")

    model = cost_model or ResearchCostModel()
    model.validate()
    result = audit.copy()
    requested = pd.to_numeric(result["requested_quantity"], errors="coerce").fillna(0.0).abs()
    filled = pd.to_numeric(result["filled_quantity"], errors="coerce").fillna(0.0).abs()
    filled_value = pd.to_numeric(result["filled_value"], errors="coerce").fillna(0.0).abs()
    actual_cost = pd.to_numeric(result["trade_cost"], errors="coerce").fillna(0.0).abs()
    target_trade = result["target_action"].isin({"BUY", "SELL"})
    requested_order = result["order_requested"].fillna(False).astype(bool)
    execution_status = result["execution_status"].astype(str)
    actual_action = result["actual_action"].astype(str)
    target_action = result["target_action"].astype(str)

    result["execution_gap"] = np.select(
        [
            ~target_trade,
            target_trade & (~requested_order | execution_status.eq("UNFILLED")),
            target_trade & execution_status.eq("PARTIAL"),
            target_trade & filled.gt(0) & actual_action.ne(target_action),
        ],
        ["NO_ACTION", "BLOCKED", "PARTIAL", "ACTION_MISMATCH"],
        default="MATCHED",
    )
    result["blocked"] = result["execution_gap"].eq("BLOCKED")
    result["partial"] = result["execution_gap"].eq("PARTIAL")

    fill_ratio = pd.Series(np.nan, index=result.index, dtype=float)
    positive_request = requested.gt(0)
    fill_ratio.loc[positive_request] = filled.loc[positive_request] / requested.loc[positive_request]
    # A strategy trade that never became a positive-sized order represents a
    # full turnover loss, not an unknown ratio. Non-trade rows stay NaN.
    zero_sized_trade = target_trade & ~positive_request
    fill_ratio.loc[zero_sized_trade] = 0.0
    result["fill_ratio"] = fill_ratio
    result["turnover_drift_ratio"] = np.where(target_trade, fill_ratio - 1.0, 0.0)

    if model.calibrated:
        expected = filled_value * float(model.expected_cost_bps) / 10_000.0
        result["expected_trade_cost"] = expected
        result["cost_drift"] = actual_cost - expected
        result["cost_drift_status"] = "CALCULATED"
    else:
        result["expected_trade_cost"] = np.nan
        result["cost_drift"] = np.nan
        result["cost_drift_status"] = "UNAVAILABLE_UNCALIBRATED"
    result["cost_model_id"] = model.model_id
    return result


def summarize_strategy_execution_attribution(
    attribution: pd.DataFrame,
) -> StrategyExecutionAttribution:
    """Produce a compact evidence summary from an attributed StrategyAudit."""

    required = {
        "execution_gap",
        "requested_quantity",
        "filled_quantity",
        "trade_cost",
        "expected_trade_cost",
        "cost_drift",
        "cost_drift_status",
    }
    missing = required - set(attribution.columns)
    if missing:
        raise ValueError(f"attribution missing columns: {sorted(missing)}")

    requested = float(pd.to_numeric(attribution["requested_quantity"], errors="coerce").fillna(0).abs().sum())
    filled = float(pd.to_numeric(attribution["filled_quantity"], errors="coerce").fillna(0).abs().sum())
    turnover_drift = (filled / requested - 1.0) if requested > 0 else None
    actual_cost = float(pd.to_numeric(attribution["trade_cost"], errors="coerce").fillna(0).abs().sum())
    expected_series = pd.to_numeric(attribution["expected_trade_cost"], errors="coerce")
    drift_series = pd.to_numeric(attribution["cost_drift"], errors="coerce")
    calibrated = attribution["cost_drift_status"].eq("CALCULATED").any()
    expected_cost = float(expected_series.fillna(0).sum()) if calibrated else None
    cost_drift = float(drift_series.fillna(0).sum()) if calibrated else None

    gap = attribution["execution_gap"].astype(str)
    return StrategyExecutionAttribution(
        rows=len(attribution),
        matched=int(gap.eq("MATCHED").sum()),
        blocked=int(gap.eq("BLOCKED").sum()),
        partial=int(gap.eq("PARTIAL").sum()),
        action_mismatch=int(gap.eq("ACTION_MISMATCH").sum()),
        requested_quantity=requested,
        filled_quantity=filled,
        turnover_drift_ratio=turnover_drift,
        actual_cost=actual_cost,
        expected_cost=expected_cost,
        cost_drift=cost_drift,
        cost_model_status="CALIBRATED" if calibrated else "UNAVAILABLE_UNCALIBRATED",
    )
