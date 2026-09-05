from __future__ import annotations

import numpy as np
import pandas as pd

from qlib_platform.backtesting.ashare_costs import execution_fees, impacted_fill_price
from qlib_platform.backtesting.ashare_rules import AShareMarketRules, as_bool, resolve_limits
from qlib_platform.backtesting.ashare_state import SimulationState


def _spread_bps(row: pd.Series, rules: AShareMarketRules) -> float:
    value = pd.to_numeric(pd.Series([row.get("spread_bps")]), errors="coerce").iloc[0]
    return float(value) if pd.notna(value) else rules.default_spread_bps


def _order_limit_allows(order: pd.Series, side: str, price: float) -> bool:
    if "limit_price" not in order or pd.isna(order.get("limit_price")):
        return True
    limit = float(order["limit_price"])
    return not ((side == "BUY" and price > limit) or (side == "SELL" and price < limit))


def execute_order(
    order: pd.Series,
    row: pd.Series,
    *,
    rules: AShareMarketRules,
    state: SimulationState,
    next_trade_date: pd.Timestamp | None,
) -> None:
    trade_date = pd.Timestamp(order["trade_date"])
    instrument = str(order["instrument"])
    side = str(order["side"])
    requested = int(order["quantity"])
    key = (trade_date, instrument)
    reference = float(row[rules.deal_price_column])
    state.requested_notional += requested * reference

    daily_capacity = int(np.floor(float(row["volume"]) * rules.max_participation_rate))
    remaining_capacity = max(0, daily_capacity - state.volume_used[key])
    capacity_notional = daily_capacity * reference
    if key not in state.capacity_counted:
        state.total_capacity_notional += capacity_notional
        state.capacity_counted.add(key)

    if as_bool(row, "paused") or float(row["volume"]) <= 0:
        state.reject(order, "suspended_or_zero_volume", requested)
        return
    if side == "BUY" and (as_bool(row, "is_limit_up") or as_bool(row, "limit_up_locked")):
        state.reject(order, "limit_up_no_buy_liquidity", requested)
        return
    if side == "SELL" and (as_bool(row, "is_limit_down") or as_bool(row, "limit_down_locked")):
        state.reject(order, "limit_down_no_sell_liquidity", requested)
        return

    limit_up, limit_down = resolve_limits(row, rules)
    quantity = min(requested, remaining_capacity)
    if side == "BUY":
        quantity = (quantity // rules.buy_lot_size) * rules.buy_lot_size
    else:
        quantity = min(quantity, state.positions[instrument].available)
    if quantity <= 0:
        reason = "t_plus_one_or_no_position" if side == "SELL" else "volume_below_buy_lot"
        state.reject(order, reason, requested)
        return

    price, impact_bps = impacted_fill_price(
        reference,
        side=side,
        quantity=quantity,
        volume=float(row["volume"]),
        spread_bps=_spread_bps(row, rules),
        rules=rules,
        limit_up=limit_up,
        limit_down=limit_down,
    )
    if not _order_limit_allows(order, side, price):
        state.reject(order, "order_limit_not_marketable", requested)
        return

    if side == "BUY":
        while quantity > 0:
            notional = quantity * price
            fee = execution_fees(notional, side, rules)
            if notional + fee <= state.cash + 1e-9:
                break
            quantity -= rules.buy_lot_size
        if quantity <= 0:
            state.reject(order, "insufficient_cash", requested)
            return
        notional = quantity * price
        fee = execution_fees(notional, side, rules)
        state.cash -= notional + fee
        state.positions[instrument].total += quantity
        if next_trade_date is not None:
            state.unlocks[next_trade_date].append((instrument, quantity))
    else:
        notional = quantity * price
        fee = execution_fees(notional, side, rules)
        state.positions[instrument].total -= quantity
        state.positions[instrument].available -= quantity
        state.cash += notional - fee

    state.volume_used[key] += quantity
    state.filled_notional += notional
    state.fills.append(
        {
            "order_id": order["order_id"],
            "trade_date": trade_date,
            "instrument": instrument,
            "side": side,
            "requested_quantity": requested,
            "filled_quantity": quantity,
            "partial_fill": quantity < requested,
            "reference_price": reference,
            "fill_price": price,
            "notional": notional,
            "fees": fee,
            "participation_rate": quantity / float(row["volume"]),
            "impact_bps": impact_bps,
            "capacity_quantity_before_order": remaining_capacity,
            "daily_capacity_quantity": daily_capacity,
            "capacity_notional": capacity_notional,
            "limit_up": limit_up,
            "limit_down": limit_down,
        }
    )
