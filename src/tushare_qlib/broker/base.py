from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

import pandas as pd


@dataclass(frozen=True)
class BrokerSnapshot:
    """A point-in-time, read-only view of a broker account."""

    account: Mapping[str, Any]
    positions: pd.DataFrame
    orders: pd.DataFrame
    fills: pd.DataFrame
    initial_holdings: pd.DataFrame | None = None


@runtime_checkable
class ReadOnlyBrokerAdapter(Protocol):
    """Broker boundary intentionally has no order submission operation."""

    source_name: str

    def snapshot(self, trade_date: str) -> BrokerSnapshot: ...


def validate_broker_snapshot(snapshot: BrokerSnapshot, trade_date: str) -> BrokerSnapshot:
    required_account = {
        "as_of_trade_date",
        "snapshot_at_utc",
        "portfolio_value",
        "cash",
        "daily_pnl_pct",
    }
    missing_account = required_account - set(snapshot.account)
    if missing_account:
        raise ValueError(f"account snapshot missing fields: {sorted(missing_account)}")
    if pd.Timestamp(str(snapshot.account["as_of_trade_date"])).normalize() != pd.Timestamp(
        trade_date
    ).normalize():
        raise ValueError("account snapshot trade date does not match the requested trade date")
    if not {"instrument", "quantity", "available_quantity"}.issubset(snapshot.positions.columns):
        raise ValueError("positions snapshot is missing instrument/quantity/available_quantity")
    for name, frame in (("orders", snapshot.orders), ("fills", snapshot.fills)):
        if not isinstance(frame, pd.DataFrame):
            raise TypeError(f"{name} snapshot must be a DataFrame")
    return snapshot
