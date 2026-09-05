from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass
class PositionState:
    total: int = 0
    available: int = 0


@dataclass(frozen=True)
class AShareSimulationResult:
    fills: pd.DataFrame
    rejections: pd.DataFrame
    daily_account: pd.DataFrame
    positions: pd.DataFrame
    summary: dict[str, Any]


class SimulationState:
    def __init__(self, initial_cash: float) -> None:
        self.cash = float(initial_cash)
        self.positions: defaultdict[str, PositionState] = defaultdict(PositionState)
        self.unlocks: defaultdict[pd.Timestamp, list[tuple[str, int]]] = defaultdict(list)
        self.fills: list[dict[str, Any]] = []
        self.rejections: list[dict[str, Any]] = []
        self.account_rows: list[dict[str, Any]] = []
        self.rejection_counts: Counter[str] = Counter()
        self.volume_used: defaultdict[tuple[pd.Timestamp, str], int] = defaultdict(int)
        self.capacity_counted: set[tuple[pd.Timestamp, str]] = set()
        self.requested_notional = 0.0
        self.filled_notional = 0.0
        self.total_capacity_notional = 0.0

    def release_t_plus_one(self, trade_date: pd.Timestamp) -> None:
        for instrument, quantity in self.unlocks.pop(trade_date, []):
            self.positions[instrument].available += quantity

    def reject(self, order: pd.Series, reason: str, requested: int) -> None:
        self.rejection_counts[reason] += 1
        self.rejections.append(
            {
                "order_id": order["order_id"],
                "trade_date": order["trade_date"],
                "instrument": order["instrument"],
                "side": order["side"],
                "requested_quantity": requested,
                "reason": reason,
            }
        )
