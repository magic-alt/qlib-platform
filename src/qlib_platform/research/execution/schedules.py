from __future__ import annotations

from math import floor, isfinite

import pandas as pd

from qlib_platform.research.execution.market_data import order_window
from qlib_platform.research.execution.types import ExecutionStrategy, ParentOrder


def _allocate_integer(total: int, weights: list[float]) -> list[int]:
    if total < 0:
        raise ValueError("allocation total must be non-negative")
    if not weights:
        raise ValueError("allocation weights must be non-empty")
    if any(not isfinite(weight) or weight < 0 for weight in weights):
        raise ValueError("allocation weights must be finite and non-negative")
    weight_sum = sum(weights)
    if weight_sum <= 0:
        raise ValueError("allocation weights must contain positive mass")

    raw = [total * weight / weight_sum for weight in weights]
    allocated = [floor(value) for value in raw]
    remainder = total - sum(allocated)
    priority = sorted(
        range(len(raw)),
        key=lambda index: (-(raw[index] - allocated[index]), index),
    )
    for index in priority[:remainder]:
        allocated[index] += 1
    return allocated


def _schedule_frame(
    order: ParentOrder, window: pd.DataFrame, quantities: list[int], strategy: str
) -> pd.DataFrame:
    if len(window) != len(quantities):
        raise ValueError("schedule quantities must align with the intraday window")
    remaining = order.quantity
    records: list[dict[str, object]] = []
    for row_index, quantity in enumerate(quantities):
        remaining -= quantity
        row = window.iloc[row_index]
        records.append(
            {
                "parent_order_id": order.order_id,
                "timestamp": row["timestamp"],
                "instrument": order.instrument,
                "side": order.side,
                "target_quantity": int(quantity),
                "strategy": strategy,
                "market_volume": float(row["volume"]),
                "remaining_quantity_after": int(max(remaining, 0)),
            }
        )
    return pd.DataFrame.from_records(records)


def build_twap_schedule(order: ParentOrder, bars: pd.DataFrame) -> pd.DataFrame:
    window = order_window(bars, order)
    quantities = _allocate_integer(order.quantity, [1.0] * len(window))
    return _schedule_frame(order, window, quantities, "twap")


def build_vwap_schedule(order: ParentOrder, bars: pd.DataFrame) -> pd.DataFrame:
    """Build an ex-post realized-volume research benchmark schedule.

    This is deliberately not a live volume forecast. It answers the research question
    "how would the parent order have been distributed if realized intraday volume were known?"
    """

    window = order_window(bars, order)
    volumes = [float(value) for value in window["volume"].tolist()]
    quantities = _allocate_integer(order.quantity, volumes)
    return _schedule_frame(order, window, quantities, "vwap")


def build_pov_schedule(
    order: ParentOrder,
    bars: pd.DataFrame,
    *,
    participation_rate: float,
) -> pd.DataFrame:
    if not isfinite(participation_rate) or not 0 < participation_rate <= 1:
        raise ValueError("participation_rate must be finite and in (0, 1]")
    window = order_window(bars, order)
    remaining = order.quantity
    quantities: list[int] = []
    for volume in window["volume"].tolist():
        capacity = max(0, floor(float(volume) * participation_rate))
        quantity = min(remaining, capacity)
        quantities.append(quantity)
        remaining -= quantity
    return _schedule_frame(order, window, quantities, "pov")


def build_execution_schedule(
    order: ParentOrder,
    bars: pd.DataFrame,
    *,
    strategy: ExecutionStrategy,
    pov_rate: float = 0.10,
) -> pd.DataFrame:
    if strategy == "twap":
        return build_twap_schedule(order, bars)
    if strategy == "vwap":
        return build_vwap_schedule(order, bars)
    if strategy == "pov":
        return build_pov_schedule(order, bars, participation_rate=pov_rate)
    raise ValueError(f"unsupported execution strategy: {strategy}")
