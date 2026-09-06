from __future__ import annotations

from math import floor, sqrt

import numpy as np
import pandas as pd

from qlib_platform.research.execution.market_data import execution_benchmarks, normalize_intraday_bars
from qlib_platform.research.execution.schedules import build_execution_schedule
from qlib_platform.research.execution.types import (
    ExecutionModelConfig,
    ExecutionSimulationResult,
    ExecutionStrategy,
    ParentOrder,
)


def expected_fill_probability(
    requested_quantity: int,
    market_volume: float,
    queue_ahead_quantity: float,
    config: ExecutionModelConfig,
) -> float:
    if requested_quantity <= 0 or market_volume <= 0:
        return 0.0
    gross_capacity = market_volume * config.max_participation_rate
    effective_queue = queue_ahead_quantity * config.queue_liquidity_fraction
    accessible_capacity = max(0.0, gross_capacity - effective_queue)
    return float(min(1.0, accessible_capacity / requested_quantity))


def simulate_schedule(
    order: ParentOrder,
    bars: pd.DataFrame,
    schedule: pd.DataFrame,
    *,
    config: ExecutionModelConfig | None = None,
) -> ExecutionSimulationResult:
    resolved = config or ExecutionModelConfig()
    required_schedule = {
        "parent_order_id",
        "timestamp",
        "instrument",
        "side",
        "target_quantity",
        "strategy",
    }
    missing = sorted(required_schedule.difference(schedule.columns))
    if missing:
        raise ValueError(f"execution schedule missing required columns: {missing}")
    if bool((pd.to_numeric(schedule["target_quantity"], errors="coerce").isna()).any()):
        raise ValueError("target_quantity must be numeric")
    if bool((pd.to_numeric(schedule["target_quantity"], errors="coerce") < 0).any()):
        raise ValueError("target_quantity must be non-negative")
    if bool((schedule["parent_order_id"].astype(str) != order.order_id).any()):
        raise ValueError("schedule parent_order_id must match the parent order")
    if bool((schedule["instrument"].astype(str) != order.instrument).any()):
        raise ValueError("schedule instrument must match the parent order")
    if bool((schedule["side"].astype(str) != order.side).any()):
        raise ValueError("schedule side must match the parent order")

    normalized = normalize_intraday_bars(bars)
    merged = schedule.copy()
    merged["timestamp"] = pd.to_datetime(merged["timestamp"], errors="coerce")
    if bool(merged["timestamp"].isna().any()):
        raise ValueError("schedule contains invalid timestamps")
    merged = merged.merge(
        normalized[
            [
                "timestamp",
                "instrument",
                "mid_price",
                "volume",
                "spread_bps",
                "queue_ahead_quantity",
                "market_open",
                "rejected",
            ]
        ],
        on=["timestamp", "instrument"],
        how="left",
        validate="one_to_one",
    )
    if bool(merged["mid_price"].isna().any()):
        raise ValueError("every scheduled child must align to an intraday market-data bar")
    if "canceled" not in merged.columns:
        merged["canceled"] = False
    merged["canceled"] = merged["canceled"].astype(bool)

    direction = 1.0 if order.side == "BUY" else -1.0
    fill_records: list[dict[str, object]] = []
    total_filled = 0
    weighted_fill_value = 0.0
    total_fees = 0.0
    weighted_impact = 0.0
    weighted_slippage = 0.0
    partial_children = 0
    rejected_children = 0
    canceled_children = 0
    total_capacity = 0

    for _, row in merged.iterrows():
        target_quantity = int(row["target_quantity"])
        market_volume = float(row["volume"])
        queue_ahead = float(row["queue_ahead_quantity"])
        mid_price = float(row["mid_price"])
        spread_value = row["spread_bps"]
        spread_bps = resolved.default_spread_bps if pd.isna(spread_value) else float(spread_value)
        gross_capacity = max(0, floor(market_volume * resolved.max_participation_rate))
        total_capacity += gross_capacity

        status = "FILLED"
        fill_probability = 0.0
        filled_quantity = 0
        if target_quantity == 0:
            status = "SKIPPED"
        elif bool(row["canceled"]):
            status = "CANCELED"
            canceled_children += 1
        elif not bool(row["market_open"]) or bool(row["rejected"]):
            status = "REJECTED"
            rejected_children += 1
        else:
            fill_probability = expected_fill_probability(
                target_quantity,
                market_volume,
                queue_ahead,
                resolved,
            )
            filled_quantity = min(target_quantity, floor(target_quantity * fill_probability))
            if filled_quantity < target_quantity:
                status = "PARTIAL" if filled_quantity > 0 else "UNFILLED"
                partial_children += int(filled_quantity > 0)

        participation_rate = filled_quantity / market_volume if market_volume > 0 else 0.0
        if filled_quantity > 0:
            normalized_participation = participation_rate / resolved.max_participation_rate
            impact_bps = resolved.impact_coefficient_bps * sqrt(max(0.0, normalized_participation))
            latency_bps = resolved.latency_bps_per_second * resolved.latency_ms / 1_000.0
            adverse_bps = spread_bps / 2.0 + impact_bps + latency_bps
            fill_price = mid_price * (1.0 + direction * adverse_bps / 10_000.0)
            fees = filled_quantity * fill_price * resolved.fee_bps / 10_000.0
            signed_slippage_bps = direction * (fill_price - mid_price) / mid_price * 10_000.0
        else:
            impact_bps = 0.0
            latency_bps = 0.0
            fill_price = np.nan
            fees = 0.0
            signed_slippage_bps = 0.0

        total_filled += filled_quantity
        if filled_quantity > 0:
            weighted_fill_value += filled_quantity * float(fill_price)
            weighted_impact += filled_quantity * impact_bps
            weighted_slippage += filled_quantity * signed_slippage_bps
        total_fees += fees
        fill_records.append(
            {
                "parent_order_id": order.order_id,
                "timestamp": row["timestamp"],
                "instrument": order.instrument,
                "side": order.side,
                "strategy": row["strategy"],
                "target_quantity": target_quantity,
                "filled_quantity": filled_quantity,
                "status": status,
                "partial_fill": bool(0 < filled_quantity < target_quantity),
                "reference_price": mid_price,
                "fill_price": fill_price,
                "fill_probability": fill_probability,
                "market_volume": market_volume,
                "capacity_quantity": gross_capacity,
                "participation_rate": participation_rate,
                "queue_ahead_quantity": queue_ahead,
                "spread_bps": spread_bps,
                "impact_bps": impact_bps,
                "latency_bps": latency_bps,
                "slippage_bps": signed_slippage_bps,
                "fees": fees,
            }
        )

    fills = pd.DataFrame.from_records(fill_records)
    benchmarks = execution_benchmarks(bars, order)
    scheduled_quantity = int(pd.to_numeric(schedule["target_quantity"], errors="raise").sum())
    unfilled_quantity = max(0, order.quantity - total_filled)
    average_fill = weighted_fill_value / total_filled if total_filled else np.nan
    summary: dict[str, float | int | str] = {
        "parent_order_id": order.order_id,
        "requested_quantity": order.quantity,
        "scheduled_quantity": scheduled_quantity,
        "unscheduled_quantity": max(0, order.quantity - scheduled_quantity),
        "filled_quantity": total_filled,
        "unfilled_quantity": unfilled_quantity,
        "fill_ratio": total_filled / order.quantity,
        "average_fill_price": float(average_fill) if total_filled else "NA",
        "fees": total_fees,
        "weighted_impact_bps": weighted_impact / total_filled if total_filled else 0.0,
        "weighted_slippage_bps": weighted_slippage / total_filled if total_filled else 0.0,
        "partial_child_count": partial_children,
        "rejected_child_count": rejected_children,
        "canceled_child_count": canceled_children,
        "aggregate_capacity_quantity": total_capacity,
        "capacity_utilization": total_filled / total_capacity if total_capacity else 0.0,
    }
    return ExecutionSimulationResult(
        schedule=schedule.copy(),
        fills=fills,
        benchmarks=benchmarks,
        summary=summary,
    )


def simulate_execution(
    order: ParentOrder,
    bars: pd.DataFrame,
    *,
    strategy: ExecutionStrategy = "twap",
    config: ExecutionModelConfig | None = None,
    pov_rate: float = 0.10,
) -> ExecutionSimulationResult:
    schedule = build_execution_schedule(order, bars, strategy=strategy, pov_rate=pov_rate)
    return simulate_schedule(order, bars, schedule, config=config)
