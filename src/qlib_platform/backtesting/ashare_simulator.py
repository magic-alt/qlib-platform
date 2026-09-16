from __future__ import annotations

from typing import Any

import pandas as pd

from qlib_platform.backtesting.ashare_corporate_actions import (
    apply_corporate_actions,
    normalize_corporate_actions,
)
from qlib_platform.backtesting.ashare_engine import execute_order
from qlib_platform.backtesting.ashare_rules import (
    AShareMarketRules,
    infer_price_limit_pct,
    normalize_market_data,
    normalize_orders,
)
from qlib_platform.backtesting.ashare_state import AShareSimulationResult, SimulationState

__all__ = ["AShareMarketRules", "AShareSimulationResult", "infer_price_limit_pct", "simulate_ashare_orders"]


def _next_dates(trading_dates: pd.DatetimeIndex) -> dict[pd.Timestamp, pd.Timestamp | None]:
    return {
        date: trading_dates[position + 1] if position + 1 < len(trading_dates) else None
        for position, date in enumerate(trading_dates)
    }


def _mark_account(state: SimulationState, market: pd.DataFrame, trade_date: pd.Timestamp) -> None:
    day_market = market.loc[market["trade_date"] == trade_date].set_index("instrument")
    market_value = 0.0
    for instrument, position in state.positions.items():
        if position.total <= 0:
            continue
        if instrument not in day_market.index:
            raise ValueError(f"held instrument {instrument} has no market-data row on {trade_date.date()}")
        market_value += position.total * float(day_market.loc[instrument, "close"])
    state.account_rows.append(
        {
            "trade_date": trade_date,
            "cash": state.cash,
            "market_value": market_value,
            "equity": state.cash + market_value,
        }
    )
    state.record_positions(trade_date)


def _result(
    state: SimulationState,
    *,
    initial_cash: float,
    order_count: int,
    rules: AShareMarketRules,
) -> AShareSimulationResult:
    fills = pd.DataFrame(state.fills)
    rejections = pd.DataFrame(state.rejections)
    account = pd.DataFrame(state.account_rows)
    daily_positions = pd.DataFrame(state.position_rows)
    corporate_actions = pd.DataFrame(state.corporate_action_rows)
    positions = pd.DataFrame(
        [
            {
                "instrument": instrument,
                "quantity": position.total,
                "available_quantity": position.available,
            }
            for instrument, position in sorted(state.positions.items())
            if position.total != 0 or position.available != 0
        ]
    )
    ending = float(account.iloc[-1]["equity"]) if not account.empty else float(initial_cash)
    summary: dict[str, Any] = {
        "initial_cash": float(initial_cash),
        "ending_equity": ending,
        "orders": int(order_count),
        "fills": int(len(fills)),
        "rejections": int(len(rejections)),
        "rejection_counts": dict(sorted(state.rejection_counts.items())),
        "requested_notional": float(state.requested_notional),
        "filled_notional": float(state.filled_notional),
        "fill_ratio_notional": (
            float(state.filled_notional / state.requested_notional) if state.requested_notional > 0 else 0.0
        ),
        "aggregate_capacity_notional": float(state.total_capacity_notional),
        "capacity_utilization": (
            float(state.filled_notional / state.total_capacity_notional)
            if state.total_capacity_notional > 0
            else 0.0
        ),
        "max_participation_rate": rules.max_participation_rate,
        "t_plus_one": True,
        "market_rule_set_id": rules.market_rule_set_id,
        "market_rule_set_sha256": rules.market_rule_set.fingerprint,
        "cost_model_id": rules.cost_model_id,
        "fill_model_id": rules.fill_model_id,
        "price_basis": rules.price_basis,
        "corporate_action_mode": rules.market_rule_set.corporate_action_mode,
        "corporate_action_events": int(len(corporate_actions)),
    }
    return AShareSimulationResult(
        fills=fills,
        rejections=rejections,
        daily_account=account,
        positions=positions,
        summary=summary,
        daily_positions=daily_positions,
        corporate_actions=corporate_actions,
    )


def simulate_ashare_orders(
    bars: pd.DataFrame,
    orders: pd.DataFrame,
    *,
    initial_cash: float = 500_000.0,
    rules: AShareMarketRules | None = None,
    corporate_actions: pd.DataFrame | None = None,
) -> AShareSimulationResult:
    """Research-only A-share simulator with versioned deterministic market rules."""

    resolved = rules or AShareMarketRules()
    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    market = normalize_market_data(bars, resolved)
    order_frame = normalize_orders(orders)
    action_frame = normalize_corporate_actions(corporate_actions)
    if not action_frame.empty and resolved.price_basis != "raw_unadjusted":
        raise ValueError(
            "corporate actions require raw_unadjusted execution/NAV prices; adjusted prices would double count"
        )
    market_lookup = market.set_index(["trade_date", "instrument"], drop=False)
    trading_dates = pd.DatetimeIndex(market["trade_date"].unique()).sort_values()
    unknown_dates = pd.DatetimeIndex(order_frame["trade_date"].unique()).difference(trading_dates)
    if len(unknown_dates):
        raise ValueError(f"orders reference dates absent from market data: {list(unknown_dates[:5])}")
    action_dates = pd.DatetimeIndex(action_frame["effective_date"].unique()) if not action_frame.empty else pd.DatetimeIndex([])
    unknown_action_dates = action_dates.difference(trading_dates)
    if len(unknown_action_dates):
        raise ValueError(
            "corporate actions reference dates absent from the simulator trading calendar: "
            f"{list(unknown_action_dates[:5])}"
        )

    state = SimulationState(initial_cash)
    next_date = _next_dates(trading_dates)
    for trade_date in trading_dates:
        state.release_t_plus_one(trade_date)
        apply_corporate_actions(state, action_frame, trade_date, rules=resolved)
        for _, order in order_frame.loc[order_frame["trade_date"] == trade_date].iterrows():
            key = (trade_date, str(order["instrument"]))
            if key not in market_lookup.index:
                state.reject(order, "missing_market_data", int(order["quantity"]), rules=resolved)
                continue
            row = market_lookup.loc[key]
            if isinstance(row, pd.DataFrame):  # pragma: no cover - normalized input prevents this
                row = row.iloc[0]
            execute_order(order, row, rules=resolved, state=state, next_trade_date=next_date[trade_date])
        _mark_account(state, market, trade_date)
    return _result(state, initial_cash=initial_cash, order_count=len(order_frame), rules=resolved)
