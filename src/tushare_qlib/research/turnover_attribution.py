from __future__ import annotations

from collections.abc import Mapping

import pandas as pd


REQUIRED_AUDIT_COLUMNS = {
    "trade_date",
    "instrument",
    "target_action",
    "action_reason",
    "quantity_before",
    "quantity_after",
    "order_requested",
    "requested_quantity",
    "filled_quantity",
    "filled_value",
    "trade_cost",
    "actual_action",
    "execution_status",
}


def _action_category(row: pd.Series) -> str:
    action = str(row.get("target_action") or "").upper()
    before = float(pd.to_numeric(row.get("quantity_before"), errors="coerce") or 0.0)
    after = float(pd.to_numeric(row.get("quantity_after"), errors="coerce") or 0.0)
    if action == "BUY" and before <= 0 and after > 0:
        return "ENTRY"
    if action == "SELL" and before > 0 and after <= 0:
        return "EXIT"
    return "HOLD_OR_NO_POSITION_CHANGE"


def _decision_driver(row: pd.Series) -> str:
    reason = str(row.get("action_reason") or "").upper()
    if "HOLD_THRESHOLD" in reason:
        return "HOLD_THRESHOLD"
    if "REPLACEMENT" in reason or "DROP_" in reason:
        return "RANK_REPLACEMENT"
    return "OTHER_DECISION_DRIVER"


def _truthy(value: object) -> bool:
    if pd.isna(value):
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _execution_category(row: pd.Series) -> str:
    status = str(row.get("execution_status") or "").upper()
    reason = str(row.get("action_reason") or "").upper()
    if _truthy(row.get("paused")) or "SUSPEND" in reason or "MARKET_CLOSED" in reason:
        return "SUSPENDED"
    if (
        _truthy(row.get("is_limit_up"))
        or _truthy(row.get("is_limit_down"))
        or "LIMIT" in reason
        or "NOT_TRADABLE" in reason
    ):
        return "LIMIT_BLOCKED"
    if status == "PARTIAL":
        return "PARTIAL_FILL"
    if status == "UNFILLED":
        return "UNFILLED"
    if status == "FILLED":
        return "FILLED"
    return "OTHER_EXECUTION"


def normalize_strategy_audit(
    audit: pd.DataFrame,
    *,
    fold_assignments: Mapping[pd.Timestamp, str],
) -> pd.DataFrame:
    missing = REQUIRED_AUDIT_COLUMNS - set(audit)
    if missing:
        raise ValueError(f"strategy audit is missing columns: {sorted(missing)}")
    frame = audit.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise").dt.normalize()
    if "signal_date" in frame:
        frame["signal_date"] = pd.to_datetime(frame["signal_date"], errors="coerce").dt.normalize()
    else:
        frame["signal_date"] = pd.NaT
    frame["fold"] = frame["signal_date"].map(fold_assignments)
    if frame["signal_date"].notna().any() and frame.loc[frame["signal_date"].notna(), "fold"].isna().any():
        raise ValueError("strategy audit signal dates are absent from certified rolling folds")
    for column in (
        "quantity_before",
        "quantity_after",
        "requested_quantity",
        "filled_quantity",
        "filled_value",
        "trade_cost",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0).abs()
    frame["order_requested"] = frame["order_requested"].map(_truthy)
    frame["action_category"] = frame.apply(_action_category, axis=1)
    frame["decision_driver"] = frame.apply(_decision_driver, axis=1)
    frame["execution_category"] = frame.apply(_execution_category, axis=1)
    return frame


def _scopes(
    frame: pd.DataFrame,
    regime_labels: pd.DataFrame,
) -> list[tuple[str, str, str | None, str | None, pd.DataFrame]]:
    result: list[tuple[str, str, str | None, str | None, pd.DataFrame]] = [
        ("ALL_OOS", "ALL_OOS", None, None, frame)
    ]
    for fold, block in frame.loc[frame["fold"].notna()].groupby("fold", sort=True):
        result.append(("FOLD", str(fold), None, None, block))
    regimes = regime_labels.loc[
        regime_labels["status"].eq("AVAILABLE"), ["date", "dimension", "state"]
    ].copy()
    regimes["signal_date"] = pd.to_datetime(regimes.pop("date"), errors="raise").dt.normalize()
    merged = frame.loc[frame["signal_date"].notna()].merge(
        regimes, on="signal_date", how="inner", validate="many_to_many"
    )
    for (dimension, state), block in merged.groupby(["dimension", "state"], sort=True):
        result.append(("REGIME", f"{dimension}:{state}", str(dimension), str(state), block))
    return result


def _aggregate_categories(
    block: pd.DataFrame,
    *,
    category_type: str,
    category_column: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    total_turnover = float(block["filled_value"].sum())
    total_cost = float(block["trade_cost"].sum())
    for category, selected in block.groupby(category_column, sort=True):
        requested = selected.loc[selected["order_requested"]]
        requested_quantity = float(requested["requested_quantity"].sum())
        filled_quantity = float(requested["filled_quantity"].sum())
        turnover = float(selected["filled_value"].sum())
        cost = float(selected["trade_cost"].sum())
        rows.append(
            {
                "category_type": category_type,
                "category": str(category),
                "event_count": int(len(selected)),
                "trade_count": int(selected["order_requested"].sum()),
                "turnover_value": turnover,
                "turnover_contribution": turnover / total_turnover if total_turnover > 0 else 0.0,
                "cost_value": cost,
                "cost_contribution": cost / total_cost if total_cost > 0 else 0.0,
                "fill_ratio": (
                    filled_quantity / requested_quantity if requested_quantity > 0 else float("nan")
                ),
            }
        )
    return rows


def derive_turnover_attribution(
    audit: pd.DataFrame,
    regime_labels: pd.DataFrame,
    *,
    fold_assignments: Mapping[pd.Timestamp, str],
    run_name: str,
    model: str,
    variant: str,
) -> pd.DataFrame:
    normalized = normalize_strategy_audit(audit, fold_assignments=fold_assignments)
    rows: list[dict[str, object]] = []
    for scope_type, scope, dimension, state, block in _scopes(normalized, regime_labels):
        for category_type, category_column in (
            ("ACTION", "action_category"),
            ("DECISION", "decision_driver"),
            ("EXECUTION", "execution_category"),
        ):
            for values in _aggregate_categories(
                block, category_type=category_type, category_column=category_column
            ):
                rows.append(
                    {
                        "run": run_name,
                        "model": model,
                        "variant": variant,
                        "scope_type": scope_type,
                        "scope": scope,
                        "dimension": dimension,
                        "state": state,
                        **values,
                    }
                )
    return (
        pd.DataFrame(rows)
        .sort_values(["run", "scope_type", "scope", "category_type", "category"], kind="stable")
        .reset_index(drop=True)
    )
