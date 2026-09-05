from __future__ import annotations

from typing import Any

import pandas as pd

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
            float(state.filled_notional / state.requested_notional)
            if state.requested_notional > 0
            else 0.0
        ),
        "aggregate_capacity_notional": float(state.total_capacity_notional),
        "capacity_utilization": (
            float(state.filled_notional / state.total_capacity_notional)
            if state.total_capacity_notional > 0
            else 0.0
        ),
        "max_participation_rate": rules.max_participation_rate,
        "t_plus_one": True,
    }
    return AShareSimulationResult(fills, rejections, account, positions, summary)


def simulate_ashare_orders(
    bars: pd.DataFrame,
    orders: pd.DataFrame,
    *,
    initial_cash: float = 500_000.0,
    rules: AShareMarketRules | None = None,
) -> AShareSimulationResult:
    """Research-only A-share simulator with T+1, limits, liquidity and impact."""

    resolved = rules or AShareMarketRules()
    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    market = normalize_market_data(bars, resolved)
    order_frame = normalize_orders(orders)
    market_lookup = market.set_index(["trade_date", "instrument"], drop=False)
    trading_dates = pd.DatetimeIndex(market["trade_date"].unique()).sort_values()
    unknown_dates = pd.DatetimeIndex(order_frame["trade_date"].unique()).difference(trading_dates)
    if len(unknown_dates):
        raise ValueError(f"orders reference dates absent from market data: {list(unknown_dates[:5])}")

    state = SimulationState(initial_cash)
    next_date = _next_dates(trading_dates)
    for trade_date in trading_dates:
        state.release_t_plus_one(trade_date)
        for _, order in order_frame.loc[order_frame["trade_date"] == trade_date].iterrows():
            key = (trade_date, str(order["instrument"]))
            if key not in market_lookup.index:
                state.reject(order, "missing_market_data", int(order["quantity"]))
                continue
            row = market_lookup.loc[key]
            if isinstance(row, pd.DataFrame):  # pragma: no cover - normalized input prevents this
                row = row.iloc[0]
            execute_order(order, row, rules=resolved, state=state, next_trade_date=next_date[trade_date])
        _mark_account(state, market, trade_date)
    return _result(state, initial_cash=initial_cash, order_count=len(order_frame), rules=resolved)
