from __future__ import annotations

import pandas as pd

from qlib_platform.research.execution.types import (
    ExecutionSimulationResult,
    ImplementationShortfall,
    ParentOrder,
)


def implementation_shortfall(
    order: ParentOrder,
    simulation: ExecutionSimulationResult,
) -> ImplementationShortfall:
    fills = simulation.fills
    required = {"filled_quantity", "fill_price", "fees"}
    missing = sorted(required.difference(fills.columns))
    if missing:
        raise ValueError(f"fill records missing required columns: {missing}")

    filled_quantity = int(pd.to_numeric(fills["filled_quantity"], errors="raise").sum())
    if filled_quantity < 0 or filled_quantity > order.quantity:
        raise ValueError("filled quantity must be between zero and parent quantity")
    unfilled_quantity = order.quantity - filled_quantity
    executed = fills.loc[pd.to_numeric(fills["filled_quantity"], errors="raise") > 0].copy()
    if filled_quantity:
        executed_quantity = pd.to_numeric(executed["filled_quantity"], errors="raise")
        executed_price = pd.to_numeric(executed["fill_price"], errors="raise")
        average_fill = float((executed_quantity * executed_price).sum() / filled_quantity)
    else:
        average_fill = None

    fees = float(pd.to_numeric(fills["fees"], errors="raise").sum())
    benchmarks = simulation.benchmarks
    decision_price = order.decision_price or benchmarks.arrival_price
    direction = 1.0 if order.side == "BUY" else -1.0

    delay_cost = direction * (benchmarks.arrival_price - decision_price) * order.quantity
    execution_cost = (
        direction * (average_fill - benchmarks.arrival_price) * filled_quantity
        if average_fill is not None
        else 0.0
    )
    opportunity_cost = direction * (benchmarks.end_price - benchmarks.arrival_price) * unfilled_quantity
    total_cost = delay_cost + execution_cost + opportunity_cost + fees
    decision_notional = decision_price * order.quantity
    total_shortfall_bps = total_cost / decision_notional * 10_000.0
    arrival_slippage_bps = (
        direction * (average_fill - benchmarks.arrival_price) / benchmarks.arrival_price * 10_000.0
        if average_fill is not None
        else None
    )
    vwap_slippage_bps = (
        direction * (average_fill - benchmarks.vwap) / benchmarks.vwap * 10_000.0
        if average_fill is not None
        else None
    )

    return ImplementationShortfall(
        requested_quantity=order.quantity,
        filled_quantity=filled_quantity,
        unfilled_quantity=unfilled_quantity,
        average_fill_price=average_fill,
        decision_price=decision_price,
        arrival_price=benchmarks.arrival_price,
        vwap=benchmarks.vwap,
        end_price=benchmarks.end_price,
        delay_cost=delay_cost,
        execution_cost=execution_cost,
        opportunity_cost=opportunity_cost,
        fees=fees,
        total_cost=total_cost,
        arrival_slippage_bps=arrival_slippage_bps,
        vwap_slippage_bps=vwap_slippage_bps,
        total_shortfall_bps=total_shortfall_bps,
    )
