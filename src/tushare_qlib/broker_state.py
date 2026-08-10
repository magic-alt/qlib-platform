from __future__ import annotations

from enum import Enum
from pathlib import Path

import pandas as pd


class BrokerOrderState(str, Enum):
    INTENT = "INTENT"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


_ALLOWED = {
    BrokerOrderState.INTENT: {BrokerOrderState.SUBMITTED, BrokerOrderState.REJECTED, BrokerOrderState.CANCELLED},
    BrokerOrderState.SUBMITTED: {BrokerOrderState.ACKNOWLEDGED, BrokerOrderState.PARTIALLY_FILLED, BrokerOrderState.FILLED, BrokerOrderState.REJECTED, BrokerOrderState.CANCELLED},
    BrokerOrderState.ACKNOWLEDGED: {BrokerOrderState.PARTIALLY_FILLED, BrokerOrderState.FILLED, BrokerOrderState.REJECTED, BrokerOrderState.CANCELLED},
    BrokerOrderState.PARTIALLY_FILLED: {BrokerOrderState.PARTIALLY_FILLED, BrokerOrderState.FILLED, BrokerOrderState.CANCELLED},
    BrokerOrderState.FILLED: set(), BrokerOrderState.CANCELLED: set(), BrokerOrderState.REJECTED: set(),
}


def record_broker_event(ledger_path: str | Path, order_id: str, state: str, *, event_at_utc: str, broker_order_id: str | None = None) -> pd.DataFrame:
    """Append an immutable, validated broker event; terminal orders cannot reopen."""
    target = Path(ledger_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    event = BrokerOrderState(state)
    previous = pd.read_parquet(target) if target.exists() else pd.DataFrame(columns=["order_id", "state"])
    history = previous.loc[previous["order_id"].astype(str) == str(order_id)] if not previous.empty else previous
    if not history.empty:
        prior = BrokerOrderState(str(history.iloc[-1]["state"]))
        if event not in _ALLOWED[prior]:
            raise ValueError(f"illegal broker state transition {prior.value} -> {event.value}")
    elif event is not BrokerOrderState.INTENT:
        raise ValueError("first broker event must be INTENT")
    row = pd.DataFrame([{"order_id": order_id, "state": event.value, "event_at_utc": event_at_utc,
                         "broker_order_id": broker_order_id}])
    result = pd.concat([previous, row], ignore_index=True)
    result.to_parquet(target, index=False)
    return result
