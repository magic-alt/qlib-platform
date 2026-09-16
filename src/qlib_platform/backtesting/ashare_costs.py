from __future__ import annotations

from math import sqrt
from typing import Any

from qlib_platform.backtesting.ashare_rules import AShareMarketRules


def execution_fee_breakdown(
    notional: float,
    side: str,
    rules: AShareMarketRules,
    *,
    trade_date: object | None = None,
    venue: str = "SSE_SZSE",
) -> dict[str, Any]:
    commission = max(rules.min_commission, notional * rules.commission_bps / 10_000.0)
    if trade_date is None:
        transfer_bps = rules.transfer_fee_bps
        stamp_bps = rules.sell_stamp_tax_bps
        regulatory_bps = 0.0
        handling_bps = 0.0
        regime_id = "configured_current_fee_assumption"
    else:
        regime = rules.fee_regime_for(trade_date, venue)
        transfer_bps = regime.transfer_fee_bps
        stamp_bps = regime.sell_stamp_tax_bps
        regulatory_bps = regime.regulatory_fee_bps
        handling_bps = regime.exchange_handling_fee_bps
        regime_id = regime.regime_id
    transfer = notional * transfer_bps / 10_000.0
    regulatory = notional * regulatory_bps / 10_000.0
    exchange_handling = notional * handling_bps / 10_000.0
    stamp = notional * stamp_bps / 10_000.0 if side == "SELL" else 0.0
    total = commission + transfer + regulatory + exchange_handling + stamp
    return {
        "commission": float(commission),
        "transfer_fee": float(transfer),
        "regulatory_fee": float(regulatory),
        "exchange_handling_fee": float(exchange_handling),
        "stamp_tax": float(stamp),
        "total": float(total),
        "fee_regime_id": regime_id,
        "fee_venue": str(venue).strip().upper(),
    }


def execution_fees(
    notional: float,
    side: str,
    rules: AShareMarketRules,
    *,
    trade_date: object | None = None,
    venue: str = "SSE_SZSE",
) -> float:
    return float(
        execution_fee_breakdown(
            notional,
            side,
            rules,
            trade_date=trade_date,
            venue=venue,
        )["total"]
    )


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
