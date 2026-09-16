from __future__ import annotations

import numpy as np
import pandas as pd

from qlib_platform.backtesting.ashare_costs import (
    execution_fee_breakdown,
    execution_fees,
    impacted_fill_price,
)
from qlib_platform.backtesting.ashare_rules import (
    AShareMarketRules,
    as_bool,
    fee_venue,
    lifecycle_rejection,
    normalize_buy_quantity,
    normalize_sell_quantity,
    resolve_limits,
    row_is_suspended,
)
from qlib_platform.backtesting.ashare_state import SimulationState


def _spread_bps(row: pd.Series, rules: AShareMarketRules) -> float:
    value = pd.to_numeric(pd.Series([row.get("spread_bps")]), errors="coerce").iloc[0]
    return float(value) if pd.notna(value) else rules.default_spread_bps


def _order_limit_allows(order: pd.Series, side: str, price: float) -> bool:
    if "limit_price" not in order or pd.isna(order.get("limit_price")):
        return True
    limit = float(order["limit_price"])
    return not ((side == "BUY" and price > limit) or (side == "SELL" and price < limit))


def _fit_buy_to_cash(
    instrument: str,
    quantity: int,
    price: float,
    cash: float,
    rules: AShareMarketRules,
    trade_date: pd.Timestamp,
    venue: str,
) -> int:
    """Return the largest legal buy quantity whose notional plus fees fits cash."""

    upper = normalize_buy_quantity(instrument, quantity, rules)
    if upper <= 0:
        return 0
    best = 0
    low = 1
    high = upper
    while low <= high:
        mid = (low + high) // 2
        candidate = normalize_buy_quantity(instrument, mid, rules)
        if candidate <= 0:
            low = mid + 1
            continue
        notional = candidate * price
        total_cash = notional + execution_fees(
            notional,
            "BUY",
            rules,
            trade_date=trade_date,
            venue=venue,
        )
        if total_cash <= cash + 1e-9:
            best = max(best, candidate)
            low = mid + 1
        else:
            high = mid - 1
    return best


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

    lifecycle_reason = lifecycle_rejection(row)
    if lifecycle_reason is not None:
        state.reject(order, lifecycle_reason, requested, rules=rules)
        return
    if row_is_suspended(row) or float(row["volume"]) <= 0:
        state.reject(order, "suspended_or_zero_volume", requested, rules=rules)
        return

    reference = float(row[rules.deal_price_column])
    venue = fee_venue(instrument, row.get("board"))
    state.requested_notional += requested * reference
    daily_capacity = int(np.floor(float(row["volume"]) * rules.max_participation_rate))
    remaining_capacity = max(0, daily_capacity - state.volume_used[key])
    capacity_notional = daily_capacity * reference
    if key not in state.capacity_counted:
        state.total_capacity_notional += capacity_notional
        state.capacity_counted.add(key)

    if side == "BUY" and (as_bool(row, "is_limit_up") or as_bool(row, "limit_up_locked")):
        state.reject(order, "limit_up_no_buy_liquidity", requested, rules=rules)
        return
    if side == "SELL" and (as_bool(row, "is_limit_down") or as_bool(row, "limit_down_locked")):
        state.reject(order, "limit_down_no_sell_liquidity", requested, rules=rules)
        return

    limit_up, limit_down = resolve_limits(row, rules)
    quantity = min(requested, remaining_capacity)
    if side == "BUY":
        quantity = normalize_buy_quantity(instrument, quantity, rules)
    else:
        available = state.positions[instrument].available
        quantity = normalize_sell_quantity(instrument, quantity, available, rules)
    if quantity <= 0:
        if side == "SELL":
            reason = (
                "t_plus_one_or_no_position"
                if state.positions[instrument].available <= 0
                else "illegal_sell_lot_or_odd_lot_partial"
            )
        else:
            reason = "volume_below_buy_lot"
        state.reject(order, reason, requested, rules=rules)
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
        state.reject(order, "order_limit_not_marketable", requested, rules=rules)
        return

    if side == "BUY":
        quantity = _fit_buy_to_cash(
            instrument,
            quantity,
            price,
            state.cash,
            rules,
            trade_date,
            venue,
        )
        if quantity <= 0:
            state.reject(order, "insufficient_cash", requested, rules=rules)
            return
        notional = quantity * price
        fee_detail = execution_fee_breakdown(
            notional,
            side,
            rules,
            trade_date=trade_date,
            venue=venue,
        )
        state.cash -= notional + float(fee_detail["total"])
        state.positions[instrument].total += quantity
        if next_trade_date is not None:
            state.unlocks[next_trade_date].append((instrument, quantity))
    else:
        notional = quantity * price
        fee_detail = execution_fee_breakdown(
            notional,
            side,
            rules,
            trade_date=trade_date,
            venue=venue,
        )
        state.positions[instrument].total -= quantity
        state.positions[instrument].available -= quantity
        state.cash += notional - float(fee_detail["total"])

    state.volume_used[key] += quantity
    state.filled_notional += notional
    position = state.positions[instrument]
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
            "fees": float(fee_detail["total"]),
            "commission": float(fee_detail["commission"]),
            "transfer_fee": float(fee_detail["transfer_fee"]),
            "regulatory_fee": float(fee_detail["regulatory_fee"]),
            "exchange_handling_fee": float(fee_detail["exchange_handling_fee"]),
            "stamp_tax": float(fee_detail["stamp_tax"]),
            "fee_regime_id": str(fee_detail["fee_regime_id"]),
            "fee_venue": str(fee_detail["fee_venue"]),
            "participation_rate": quantity / float(row["volume"]),
            "impact_bps": impact_bps,
            "capacity_quantity_before_order": remaining_capacity,
            "daily_capacity_quantity": daily_capacity,
            "capacity_notional": capacity_notional,
            "limit_up": limit_up,
            "limit_down": limit_down,
            "cash_after": state.cash,
            "position_total_after": position.total,
            "position_available_after": position.available,
            "market_rule_set_id": rules.market_rule_set_id,
            "cost_model_id": rules.cost_model_id,
            "fill_model_id": rules.fill_model_id,
        }
    )
