from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd

from .topk_dropout import TopkDropoutPolicy, topk_dropout_decision


def _position_frame(position: Any) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for instrument in sorted(position.get_stock_list()):
        rows.append(
            {
                "instrument": str(instrument),
                "quantity": float(position.get_stock_amount(str(instrument))),
                "holding_days": int(position.get_stock_count(str(instrument), "day")),
            }
        )
    return pd.DataFrame(rows, columns=["instrument", "quantity", "holding_days"])


def _indicator_values(indicator: Any, trade_date: pd.Timestamp, metric: str) -> dict[str, float]:
    order_indicator = indicator.order_indicator_his.get(trade_date)
    if order_indicator is None or metric not in order_indicator.data:
        return {}
    data = order_indicator.data[metric]
    return {str(instrument): float(value) for instrument, value in zip(list(data.index), data.data)}


def _trade_date_quotes(quote_status: pd.DataFrame, trade_date: pd.Timestamp) -> pd.DataFrame:
    if quote_status.empty:
        return pd.DataFrame(columns=["instrument", "paused", "is_limit_up", "is_limit_down"])
    frame = quote_status.loc[quote_status["trade_date"] == trade_date]
    return frame[["instrument", "paused", "is_limit_up", "is_limit_down"]].copy()


def _orders_match_or_tie_equivalent(
    planned: dict[str, str],
    requested: dict[str, str],
    scores: pd.Series,
    *,
    positions: pd.DataFrame | None = None,
    policy: TopkDropoutPolicy | None = None,
) -> bool:
    """Treat substitutions at an exactly tied Topk cutoff as equivalent."""
    if (set(planned.values()) | set(requested.values())) - {"BUY", "SELL"}:
        return False
    if planned == requested:
        return True
    for action in ("BUY", "SELL"):
        planned_codes = sorted(code for code, value in planned.items() if value == action)
        requested_codes = sorted(code for code, value in requested.items() if value == action)
        if len(planned_codes) != len(requested_codes):
            if action != "SELL" or positions is None or policy is None:
                return False
            if len(requested_codes) > len(planned_codes):
                return False
            planned_scores = pd.to_numeric(scores.reindex(planned_codes), errors="coerce")
            requested_scores = pd.to_numeric(scores.reindex(requested_codes), errors="coerce")
            if planned_scores.isna().any() or requested_scores.isna().any():
                return False
            remaining = list(planned_scores.to_numpy())
            for value in requested_scores.to_numpy():
                matches = [index for index, candidate in enumerate(remaining) if candidate == value]
                if not matches:
                    return False
                remaining.pop(matches[0])
            held = positions.copy()
            held["score"] = pd.to_numeric(held["instrument"].map(scores), errors="coerce")
            held["holding_days"] = pd.to_numeric(held["holding_days"], errors="coerce").fillna(0)
            excluded = set(planned_codes) | set(requested_codes)
            blocked_ties = held.loc[
                ~held["instrument"].isin(excluded) & held["holding_days"].lt(policy.hold_thresh),
                "score",
            ].tolist()
            for value in remaining:
                matches = [index for index, candidate in enumerate(blocked_ties) if candidate == value]
                if not matches:
                    return False
                blocked_ties.pop(matches[0])
            continue
        if not planned_codes and not requested_codes:
            continue
        planned_scores = pd.to_numeric(scores.reindex(planned_codes), errors="coerce").sort_values()
        requested_scores = pd.to_numeric(scores.reindex(requested_codes), errors="coerce").sort_values()
        if planned_scores.isna().any() or requested_scores.isna().any():
            return False
        if not np.array_equal(planned_scores.to_numpy(), requested_scores.to_numpy()):
            return False
    return True


def build_strategy_audit(
    scores: pd.Series,
    positions: dict[pd.Timestamp, Any],
    indicators: Any,
    quote_status: pd.DataFrame,
    *,
    policy: TopkDropoutPolicy,
    strict: bool = True,
) -> pd.DataFrame:
    """Reconstruct TopkDropout decisions and attach Qlib's actual fills.

    The Qlib recorder persists end-of-day positions plus its per-order indicator.
    Combining those with the previous trading step's cross-sectional signal makes
    the distinction between target action and actual execution explicit.
    """

    if not isinstance(scores.index, pd.MultiIndex) or "datetime" not in scores.index.names:
        raise ValueError("scores must have a datetime MultiIndex")
    if quote_status.empty:
        raise ValueError("quote_status is required for a tradability-aware audit")
    expected_quote = {"trade_date", "instrument", "paused", "is_limit_up", "is_limit_down"}
    missing_quote = expected_quote - set(quote_status.columns)
    if missing_quote:
        raise ValueError(f"quote_status missing columns: {sorted(missing_quote)}")

    score_dates = (
        pd.DatetimeIndex(scores.index.get_level_values("datetime").unique()).normalize().sort_values()
    )
    position_dates = pd.DatetimeIndex(sorted(pd.Timestamp(date).normalize() for date in positions))
    rows: list[pd.DataFrame] = []
    validation_errors: list[str] = []

    for index in range(1, len(position_dates)):
        trade_date = position_dates[index]
        previous_date = position_dates[index - 1]
        available_signals = score_dates[score_dates < trade_date]
        if available_signals.empty:
            continue
        signal_date = available_signals[-1]
        before = _position_frame(positions[previous_date])
        after = _position_frame(positions[trade_date])
        daily_scores = scores.xs(signal_date, level="datetime")
        daily_quotes = _trade_date_quotes(quote_status, trade_date)
        if daily_quotes.empty:
            # Qlib treats a date without quote records as fully suspended.  Keep
            # rows explainable while ensuring no hypothetical order is compared
            # against the Recorder on a market-closed data date.
            decision = topk_dropout_decision(
                daily_scores,
                before,
                policy=replace(policy, only_tradable=False),
                signal_date=signal_date,
                trade_date=trade_date,
            )
            decision[["candidate_tradable", "buy_tradable", "sell_tradable"]] = False
            decision["target_action"] = "HOLD"
            decision["action_reason"] = "MARKET_CLOSED_OR_NO_QUOTE"
            decision["action_order"] = pd.NA
        else:
            decision = topk_dropout_decision(
                daily_scores,
                before,
                daily_quotes,
                policy=policy,
                signal_date=signal_date,
                trade_date=trade_date,
            )
        amount = _indicator_values(indicators, trade_date, "amount")
        deal_amount = _indicator_values(indicators, trade_date, "deal_amount")
        direction = _indicator_values(indicators, trade_date, "trade_dir")
        trade_price = _indicator_values(indicators, trade_date, "trade_price")
        trade_value = _indicator_values(indicators, trade_date, "trade_value")
        trade_cost = _indicator_values(indicators, trade_date, "trade_cost")
        # Qlib records every generated Order in ``trade_dir``.  ``amount`` can
        # still be zero after board-lot rounding, so treating only non-zero
        # amounts as requests would falsely report a strategy divergence.
        requested = {instrument: ("BUY" if value > 0 else "SELL") for instrument, value in direction.items()}
        planned = {
            str(row.instrument): str(row.target_action)
            for row in decision.itertuples(index=False)
            if row.target_action in {"BUY", "SELL"}
        }
        if not _orders_match_or_tie_equivalent(
            planned,
            requested,
            daily_scores,
            positions=before,
            policy=policy,
        ):
            validation_errors.append(
                f"{trade_date:%Y-%m-%d}: planned={sorted(planned.items())} requested={sorted(requested.items())}"
            )

        before_qty = (
            before.set_index("instrument")["quantity"] if not before.empty else pd.Series(dtype=float)
        )
        after_qty = after.set_index("instrument")["quantity"] if not after.empty else pd.Series(dtype=float)
        audit = decision.copy()
        audit["quantity_before"] = audit["instrument"].map(before_qty).fillna(0.0)
        audit["quantity_after"] = audit["instrument"].map(after_qty).fillna(0.0)
        audit["order_requested"] = audit["instrument"].isin(requested)
        audit["requested_quantity"] = audit["instrument"].map(amount).abs().fillna(0.0)
        audit["filled_quantity"] = audit["instrument"].map(deal_amount).abs().fillna(0.0)
        audit["filled_price"] = audit["instrument"].map(trade_price)
        audit["filled_value"] = audit["instrument"].map(trade_value).abs().fillna(0.0)
        audit["trade_cost"] = audit["instrument"].map(trade_cost).abs().fillna(0.0)
        audit["actual_action"] = np.where(
            audit["filled_quantity"] > 1e-12,
            audit["instrument"].map(direction).fillna(1.0).gt(0).map({True: "BUY", False: "SELL"}),
            "HOLD",
        )
        audit["execution_status"] = np.select(
            [
                ~audit["order_requested"],
                audit["filled_quantity"].eq(0),
                audit["filled_quantity"].lt(audit["requested_quantity"]),
            ],
            ["NOT_REQUESTED", "UNFILLED", "PARTIAL"],
            default="FILLED",
        )
        rows.append(audit)

    if strict and validation_errors:
        preview = "; ".join(validation_errors[:3])
        raise RuntimeError(f"TopkDropout audit diverged from Qlib order requests: {preview}")
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)
