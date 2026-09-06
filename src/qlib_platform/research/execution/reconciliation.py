from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from qlib_platform.backtesting.execution_audit import ReconciliationResult, reconcile_execution


def execution_audit_from_fills(
    fills: pd.DataFrame,
    *,
    initial_quantities: Mapping[str, int] | None = None,
) -> pd.DataFrame:
    required = {"timestamp", "instrument", "side", "filled_quantity", "fill_price", "fees"}
    missing = sorted(required.difference(fills.columns))
    if missing:
        raise ValueError(f"fill records missing reconciliation columns: {missing}")
    frame = fills.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
    if bool(frame["timestamp"].isna().any()):
        raise ValueError("fill timestamps must be valid")
    frame["instrument"] = frame["instrument"].astype(str)
    frame["side"] = frame["side"].astype(str).str.upper()
    if bool(~frame["side"].isin(["BUY", "SELL"]).all()):
        raise ValueError("fill side must be BUY or SELL")
    frame["filled_quantity"] = pd.to_numeric(frame["filled_quantity"], errors="coerce")
    frame["fees"] = pd.to_numeric(frame["fees"], errors="coerce")
    if bool(frame[["filled_quantity", "fees"]].isna().any().any()):
        raise ValueError("filled_quantity and fees must be numeric")
    if bool((frame["filled_quantity"] < 0).any()) or bool((frame["fees"] < 0).any()):
        raise ValueError("filled_quantity and fees must be non-negative")

    positions = {str(key): int(value) for key, value in (initial_quantities or {}).items()}
    if any(value < 0 for value in positions.values()):
        raise ValueError("initial quantities must be non-negative")
    records: list[dict[str, object]] = []
    ordered = frame.sort_values(["timestamp", "instrument"], kind="stable")
    for _, row in ordered.iterrows():
        instrument = str(row["instrument"])
        side = str(row["side"])
        quantity = int(row["filled_quantity"])
        before = positions.get(instrument, 0)
        signed = quantity if side == "BUY" else -quantity
        after = before + signed
        if after < 0:
            raise ValueError(f"fill would create negative inventory for {instrument}")
        positions[instrument] = after
        fill_price = row["fill_price"]
        if quantity > 0:
            if pd.isna(fill_price) or float(fill_price) <= 0:
                raise ValueError("positive fills require a positive fill_price")
            filled_value = quantity * float(fill_price)
        else:
            filled_value = 0.0
        records.append(
            {
                "trade_date": pd.Timestamp(row["timestamp"]).normalize(),
                "instrument": instrument,
                "actual_action": side,
                "filled_quantity": quantity,
                "filled_value": filled_value,
                "trade_cost": float(row["fees"]),
                "quantity_before": before,
                "quantity_after": after,
            }
        )
    return pd.DataFrame.from_records(records)


def reconcile_simulated_execution(
    fills: pd.DataFrame,
    portfolio_report: pd.DataFrame,
    *,
    initial_quantities: Mapping[str, int] | None = None,
    tolerance: float = 1e-6,
) -> tuple[pd.DataFrame, ReconciliationResult]:
    audit = execution_audit_from_fills(fills, initial_quantities=initial_quantities)
    return reconcile_execution(audit, portfolio_report, tolerance=tolerance)
