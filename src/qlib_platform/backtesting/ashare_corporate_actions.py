from __future__ import annotations

import math

import pandas as pd

from qlib_platform.backtesting.ashare_rules import AShareMarketRules
from qlib_platform.backtesting.ashare_state import SimulationState


_ALLOWED_EVENT_TYPES = {"CASH_DIVIDEND", "SHARE_MULTIPLIER"}


def normalize_corporate_actions(actions: pd.DataFrame | None) -> pd.DataFrame:
    columns = [
        "event_id",
        "effective_date",
        "instrument",
        "event_type",
        "cash_per_share",
        "share_multiplier",
    ]
    if actions is None or actions.empty:
        return pd.DataFrame(columns=columns)
    required = {"effective_date", "instrument", "event_type"}
    missing = sorted(required - set(actions.columns))
    if missing:
        raise ValueError(f"corporate actions missing required columns: {missing}")
    frame = actions.copy()
    frame["effective_date"] = pd.to_datetime(frame["effective_date"], errors="coerce").dt.normalize()
    frame["instrument"] = frame["instrument"].astype(str).str.upper().str.strip()
    frame["event_type"] = frame["event_type"].astype(str).str.upper().str.strip()
    if frame["effective_date"].isna().any() or not frame["event_type"].isin(_ALLOWED_EVENT_TYPES).all():
        raise ValueError("corporate actions contain invalid effective_date or event_type")
    if "event_id" not in frame:
        frame["event_id"] = [f"corporate_action_{index:06d}" for index in range(len(frame))]
    else:
        frame["event_id"] = frame["event_id"].astype(str)
    for column in ("cash_per_share", "share_multiplier"):
        if column not in frame:
            frame[column] = pd.NA
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    cash_rows = frame["event_type"] == "CASH_DIVIDEND"
    share_rows = frame["event_type"] == "SHARE_MULTIPLIER"
    if frame.loc[cash_rows, "cash_per_share"].isna().any() or (
        frame.loc[cash_rows, "cash_per_share"] < 0
    ).any():
        raise ValueError("CASH_DIVIDEND requires non-negative cash_per_share")
    if frame.loc[share_rows, "share_multiplier"].isna().any() or (
        frame.loc[share_rows, "share_multiplier"] <= 0
    ).any():
        raise ValueError("SHARE_MULTIPLIER requires positive share_multiplier")
    if frame.duplicated(["event_id"]).any():
        raise ValueError("corporate action event_id must be unique")
    return frame[columns].sort_values(["effective_date", "event_id"]).reset_index(drop=True)


def _scaled_quantity(quantity: int, multiplier: float, *, event_id: str) -> int:
    result = quantity * multiplier
    rounded = round(result)
    if not math.isclose(result, rounded, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError(
            f"corporate action {event_id} produces fractional raw shares; explicit cash-in-lieu evidence is required"
        )
    return int(rounded)


def apply_corporate_actions(
    state: SimulationState,
    actions: pd.DataFrame,
    trade_date: pd.Timestamp,
    *,
    rules: AShareMarketRules,
) -> None:
    if actions.empty:
        return
    if rules.price_basis != "raw_unadjusted":
        raise ValueError(
            "corporate actions require raw_unadjusted execution/NAV prices; adjusted prices would double count the event"
        )
    for row in actions.loc[actions["effective_date"] == trade_date].itertuples(index=False):
        instrument = str(row.instrument)
        position = state.positions[instrument]
        before_total = position.total
        before_available = position.available
        cash_before = state.cash
        cash_delta = 0.0
        event_type = str(row.event_type)
        if event_type == "CASH_DIVIDEND":
            cash_delta = before_total * float(row.cash_per_share)
            state.cash += cash_delta
        elif event_type == "SHARE_MULTIPLIER":
            multiplier = float(row.share_multiplier)
            position.total = _scaled_quantity(before_total, multiplier, event_id=str(row.event_id))
            position.available = _scaled_quantity(
                before_available,
                multiplier,
                event_id=str(row.event_id),
            )
        else:  # pragma: no cover - normalization rejects unsupported types
            raise AssertionError(event_type)
        state.corporate_action_rows.append(
            {
                "event_id": str(row.event_id),
                "effective_date": trade_date,
                "instrument": instrument,
                "event_type": event_type,
                "cash_per_share": (
                    float(row.cash_per_share) if pd.notna(row.cash_per_share) else None
                ),
                "share_multiplier": (
                    float(row.share_multiplier) if pd.notna(row.share_multiplier) else None
                ),
                "cash_before": cash_before,
                "cash_delta": cash_delta,
                "cash_after": state.cash,
                "quantity_before": before_total,
                "quantity_after": position.total,
                "available_before": before_available,
                "available_after": position.available,
                "market_rule_set_id": rules.market_rule_set_id,
                "corporate_action_mode": rules.market_rule_set.corporate_action_mode,
            }
        )
