from __future__ import annotations

from math import sqrt

from qlib_platform.backtesting.ashare_rules import AShareMarketRules


def execution_fees(notional: float, side: str, rules: AShareMarketRules) -> float:
    commission = max(rules.min_commission, notional * rules.commission_bps / 10_000.0)
    transfer = notional * rules.transfer_fee_bps / 10_000.0
    stamp = notional * rules.sell_stamp_tax_bps / 10_000.0 if side == "SELL" else 0.0
    return commission + transfer + stamp


def impacted_fill_price(
    reference: float,
    *,
    side: str,
    quantity: int,
    volume: float,
    spread_bps: float,
    rules: AShareMarketRules,
    limit_up: float | None,
    limit_down: float | None,
) -> tuple[float, float]:
    participation = min(1.0, quantity / max(volume, 1.0))
    impact_bps = rules.impact_bps_at_full_participation * sqrt(participation)
    total_bps = spread_bps / 2.0 + rules.slippage_bps + impact_bps
    direction = 1.0 if side == "BUY" else -1.0
    price = reference * (1.0 + direction * total_bps / 10_000.0)
    if limit_up is not None:
        price = min(price, limit_up)
    if limit_down is not None:
        price = max(price, limit_down)
    return float(price), float(impact_bps)
