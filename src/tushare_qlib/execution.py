from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ExecutionPolicy:
    board_lot: int = 100
    max_participation_rate: float = 0.05
    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_duty_sell: float = 0.0005
    transfer_fee_rate: float = 0.00001
    price_buffer_buy: float = 0.002
    price_buffer_sell: float = 0.002
    block_limit_up_buy: bool = True
    block_limit_down_sell: bool = True

    @classmethod
    def from_mapping(cls, data: Mapping[str, object] | None) -> "ExecutionPolicy":
        data = data or {}
        return cls(
            board_lot=int(data.get("board_lot", cls.board_lot)),
            max_participation_rate=float(data.get("max_participation_rate", cls.max_participation_rate)),
            commission_rate=float(data.get("commission_rate", cls.commission_rate)),
            min_commission=float(data.get("min_commission", cls.min_commission)),
            stamp_duty_sell=float(data.get("stamp_duty_sell", cls.stamp_duty_sell)),
            transfer_fee_rate=float(data.get("transfer_fee_rate", cls.transfer_fee_rate)),
            price_buffer_buy=float(data.get("price_buffer_buy", cls.price_buffer_buy)),
            price_buffer_sell=float(data.get("price_buffer_sell", cls.price_buffer_sell)),
            block_limit_up_buy=bool(data.get("block_limit_up_buy", cls.block_limit_up_buy)),
            block_limit_down_sell=bool(data.get("block_limit_down_sell", cls.block_limit_down_sell)),
        )


def _as_bool(value: object) -> bool:
    if pd.isna(value):
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _order_id(trade_date: str, instrument: str, side: str, quantity: int, model_id: str) -> str:
    raw = f"{trade_date}|{instrument}|{side}|{quantity}|{model_id}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def _fees(notional: float, side: str, policy: ExecutionPolicy) -> float:
    commission = max(policy.min_commission, notional * policy.commission_rate) if notional > 0 else 0.0
    transfer = notional * policy.transfer_fee_rate
    stamp = notional * policy.stamp_duty_sell if side == "SELL" else 0.0
    return commission + transfer + stamp


def build_orders(
    targets: pd.DataFrame,
    positions: pd.DataFrame,
    quotes: pd.DataFrame,
    *,
    trade_date: str,
    portfolio_value: float,
    cash: float,
    policy: ExecutionPolicy | None = None,
    model_id: str = "unversioned",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Convert target weights to broker-neutral A-share orders.

    `available_quantity` is mandatory for positions and represents T+1 sellable inventory.
    Quotes are point-in-time execution snapshots, not research close prices.
    """

    policy = policy or ExecutionPolicy()
    if portfolio_value <= 0:
        raise ValueError("portfolio_value must be positive")
    required_target = {"instrument", "target_weight"}
    required_position = {"instrument", "quantity", "available_quantity"}
    required_quote = {"instrument", "price", "paused", "is_limit_up", "is_limit_down"}
    for name, frame, required in (
        ("targets", targets, required_target),
        ("positions", positions, required_position),
        ("quotes", quotes, required_quote),
    ):
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"{name} missing columns: {sorted(missing)}")

    t = targets.copy().drop_duplicates("instrument", keep="first").set_index("instrument")
    p = positions.copy().drop_duplicates("instrument", keep="last").set_index("instrument")
    q = quotes.copy().drop_duplicates("instrument", keep="last").set_index("instrument")
    universe = t.index.union(p.index)
    rows: list[dict[str, object]] = []
    blocked: list[dict[str, object]] = []

    for instrument in universe:
        target_weight = float(pd.to_numeric(t.at[instrument, "target_weight"], errors="coerce")) if instrument in t.index else 0.0
        current_qty = int(pd.to_numeric(p.at[instrument, "quantity"], errors="coerce")) if instrument in p.index else 0
        available_qty = int(pd.to_numeric(p.at[instrument, "available_quantity"], errors="coerce")) if instrument in p.index else 0
        if instrument not in q.index:
            blocked.append({"instrument": instrument, "reason": "MISSING_QUOTE", "requested_delta": None})
            continue
        quote = q.loc[instrument]
        price = float(pd.to_numeric(quote["price"], errors="coerce"))
        if not np.isfinite(price) or price <= 0:
            blocked.append({"instrument": instrument, "reason": "INVALID_PRICE", "requested_delta": None})
            continue
        lot = int(pd.to_numeric(quote.get("board_lot", policy.board_lot), errors="coerce"))
        lot = lot if lot > 0 else policy.board_lot
        target_qty = int(np.floor((portfolio_value * max(0.0, target_weight)) / price / lot) * lot)
        delta = target_qty - current_qty
        if delta == 0:
            continue
        side = "BUY" if delta > 0 else "SELL"
        requested = abs(delta)
        reasons: list[str] = []
        if _as_bool(quote["paused"]):
            reasons.append("PAUSED")
        if side == "BUY" and policy.block_limit_up_buy and _as_bool(quote["is_limit_up"]):
            reasons.append("LIMIT_UP")
        if side == "SELL" and policy.block_limit_down_sell and _as_bool(quote["is_limit_down"]):
            reasons.append("LIMIT_DOWN")
        if reasons:
            blocked.append({"instrument": instrument, "reason": "+".join(reasons), "requested_delta": delta})
            continue

        quantity = requested
        if side == "SELL":
            quantity = min(quantity, max(0, available_qty))
            if quantity <= 0:
                blocked.append({"instrument": instrument, "reason": "T1_NOT_SELLABLE", "requested_delta": delta})
                continue
        adv20_volume = pd.to_numeric(quote.get("adv20_volume", np.nan), errors="coerce")
        if np.isfinite(adv20_volume) and adv20_volume > 0:
            participation_cap = int(np.floor(float(adv20_volume) * policy.max_participation_rate / lot) * lot)
            quantity = min(quantity, participation_cap)
        quantity = int(np.floor(quantity / lot) * lot)
        if quantity <= 0:
            blocked.append({"instrument": instrument, "reason": "BELOW_LOT_OR_LIQUIDITY_CAP", "requested_delta": delta})
            continue

        limit_price = price * (1 + policy.price_buffer_buy) if side == "BUY" else price * (1 - policy.price_buffer_sell)
        notional = quantity * limit_price
        fee = _fees(notional, side, policy)
        score = float(t.at[instrument, "score"]) if instrument in t.index and "score" in t.columns else np.nan
        rows.append(
            {
                "trade_date": trade_date,
                "instrument": instrument,
                "side": side,
                "quantity": quantity,
                "limit_price": round(limit_price, 4),
                "estimated_notional": round(notional, 2),
                "estimated_fees": round(fee, 2),
                "target_weight": target_weight,
                "current_quantity": current_qty,
                "available_quantity": available_qty,
                "score": score,
                "client_order_id": _order_id(trade_date, str(instrument), side, quantity, model_id),
                "status": "READY",
            }
        )

    orders = pd.DataFrame(rows)
    blocked_df = pd.DataFrame(blocked)
    if orders.empty:
        return orders, blocked_df

    # Process sells first; proceeds are conservatively reduced by fees. Buys are then cash-gated by score.
    sell_mask = orders["side"] == "SELL"
    available_cash = float(cash) + float((orders.loc[sell_mask, "estimated_notional"] - orders.loc[sell_mask, "estimated_fees"]).sum())
    buys = orders.loc[~sell_mask].sort_values(["score", "instrument"], ascending=[False, True])
    accepted_buy_indices: list[int] = []
    for idx, row in buys.iterrows():
        total = float(row["estimated_notional"] + row["estimated_fees"])
        if total <= available_cash + 1e-9:
            accepted_buy_indices.append(idx)
            available_cash -= total
        else:
            blocked_df = pd.concat(
                [blocked_df, pd.DataFrame([{"instrument": row["instrument"], "reason": "INSUFFICIENT_CASH", "requested_delta": row["quantity"]}])],
                ignore_index=True,
            )
    orders = pd.concat([orders.loc[sell_mask], orders.loc[accepted_buy_indices]], ignore_index=True)
    orders = orders.sort_values(["side", "score", "instrument"], ascending=[False, False, True]).reset_index(drop=True)
    return orders, blocked_df.reset_index(drop=True)
