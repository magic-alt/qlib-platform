from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Literal

import pandas as pd

Side = Literal["BUY", "SELL"]
ExecutionStrategy = Literal["twap", "vwap", "pov"]


@dataclass(frozen=True)
class ParentOrder:
    order_id: str
    instrument: str
    side: Side
    quantity: int
    start_time: datetime
    end_time: datetime
    decision_price: float | None = None

    def __post_init__(self) -> None:
        if not self.order_id.strip():
            raise ValueError("order_id must be non-empty")
        if not self.instrument.strip():
            raise ValueError("instrument must be non-empty")
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.start_time > self.end_time:
            raise ValueError("start_time must not be after end_time")
        if self.decision_price is not None and (
            not isfinite(self.decision_price) or self.decision_price <= 0
        ):
            raise ValueError("decision_price must be finite and positive")


@dataclass(frozen=True)
class ExecutionModelConfig:
    max_participation_rate: float = 0.10
    queue_liquidity_fraction: float = 1.0
    default_spread_bps: float = 5.0
    impact_coefficient_bps: float = 15.0
    latency_ms: float = 0.0
    latency_bps_per_second: float = 0.0
    fee_bps: float = 0.0

    def __post_init__(self) -> None:
        finite_fields = {
            "max_participation_rate": self.max_participation_rate,
            "queue_liquidity_fraction": self.queue_liquidity_fraction,
            "default_spread_bps": self.default_spread_bps,
            "impact_coefficient_bps": self.impact_coefficient_bps,
            "latency_ms": self.latency_ms,
            "latency_bps_per_second": self.latency_bps_per_second,
            "fee_bps": self.fee_bps,
        }
        for name, value in finite_fields.items():
            if not isfinite(value):
                raise ValueError(f"{name} must be finite")
        if not 0 < self.max_participation_rate <= 1:
            raise ValueError("max_participation_rate must be in (0, 1]")
        if not 0 <= self.queue_liquidity_fraction <= 1:
            raise ValueError("queue_liquidity_fraction must be in [0, 1]")
        if any(
            value < 0
            for value in (
                self.default_spread_bps,
                self.impact_coefficient_bps,
                self.latency_ms,
                self.latency_bps_per_second,
                self.fee_bps,
            )
        ):
            raise ValueError("spread, impact, latency and fee parameters must be non-negative")


@dataclass(frozen=True)
class ExecutionBenchmarks:
    arrival_price: float
    vwap: float
    twap: float
    end_price: float
    total_volume: float


@dataclass(frozen=True)
class ExecutionSimulationResult:
    schedule: pd.DataFrame
    fills: pd.DataFrame
    benchmarks: ExecutionBenchmarks
    summary: dict[str, float | int | str]


@dataclass(frozen=True)
class ImplementationShortfall:
    requested_quantity: int
    filled_quantity: int
    unfilled_quantity: int
    average_fill_price: float | None
    decision_price: float
    arrival_price: float
    vwap: float
    end_price: float
    delay_cost: float
    execution_cost: float
    opportunity_cost: float
    fees: float
    total_cost: float
    arrival_slippage_bps: float | None
    vwap_slippage_bps: float | None
    total_shortfall_bps: float
