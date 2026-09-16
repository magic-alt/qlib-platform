from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:
    from qlib_platform.backtesting.ashare_rules import AShareMarketRules


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
    daily_positions: pd.DataFrame = field(default_factory=pd.DataFrame)
    corporate_actions: pd.DataFrame = field(default_factory=pd.DataFrame)


class SimulationState:
    def __init__(self, initial_cash: float) -> None:
        self.cash = float(initial_cash)
        self.positions: defaultdict[str, PositionState] = defaultdict(PositionState)
        self.unlocks: defaultdict[pd.Timestamp, list[tuple[str, int]]] = defaultdict(list)
        self.fills: list[dict[str, Any]] = []
        self.rejections: list[dict[str, Any]] = []
        self.account_rows: list[dict[str, Any]] = []
        self.position_rows: list[dict[str, Any]] = []
        self.corporate_action_rows: list[dict[str, Any]] = []
        self.rejection_counts: Counter[str] = Counter()
        self.volume_used: defaultdict[tuple[pd.Timestamp, str], int] = defaultdict(int)
        self.capacity_counted: set[tuple[pd.Timestamp, str]] = set()
        self.requested_notional = 0.0
        self.filled_notional = 0.0
        self.total_capacity_notional = 0.0

    def release_t_plus_one(self, trade_date: pd.Timestamp) -> None:
        for instrument, quantity in self.unlocks.pop(trade_date, []):
            self.positions[instrument].available += quantity

    def record_positions(self, trade_date: pd.Timestamp) -> None:
        for instrument, position in sorted(self.positions.items()):
            if position.total == 0 and position.available == 0:
                continue
            self.position_rows.append(
                {
                    "trade_date": trade_date,
                    "instrument": instrument,
                    "quantity": position.total,
                    "available_quantity": position.available,
                }
            )

    def reject(
        self,
        order: pd.Series,
        reason: str,
        requested: int,
        *,
        rules: AShareMarketRules | None = None,
    ) -> None:
        self.rejection_counts[reason] += 1
        row: dict[str, Any] = {
            "order_id": order["order_id"],
            "trade_date": order["trade_date"],
            "instrument": order["instrument"],
            "side": order["side"],
            "requested_quantity": requested,
            "reason": reason,
        }
        if rules is not None:
            row.update(
                {
                    "market_rule_set_id": rules.market_rule_set_id,
                    "cost_model_id": rules.cost_model_id,
                    "fill_model_id": rules.fill_model_id,
                }
            )
        self.rejections.append(row)
