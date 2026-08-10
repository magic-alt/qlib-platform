from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Iterator

import fcntl
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
    BrokerOrderState.INTENT: {
        BrokerOrderState.SUBMITTED,
        BrokerOrderState.REJECTED,
        BrokerOrderState.CANCELLED,
    },
    BrokerOrderState.SUBMITTED: {
        BrokerOrderState.ACKNOWLEDGED,
        BrokerOrderState.PARTIALLY_FILLED,
        BrokerOrderState.FILLED,
        BrokerOrderState.REJECTED,
        BrokerOrderState.CANCELLED,
    },
    BrokerOrderState.ACKNOWLEDGED: {
        BrokerOrderState.PARTIALLY_FILLED,
        BrokerOrderState.FILLED,
        BrokerOrderState.REJECTED,
        BrokerOrderState.CANCELLED,
    },
    BrokerOrderState.PARTIALLY_FILLED: {
        BrokerOrderState.PARTIALLY_FILLED,
        BrokerOrderState.FILLED,
        BrokerOrderState.CANCELLED,
    },
    BrokerOrderState.FILLED: set(),
    BrokerOrderState.CANCELLED: set(),
    BrokerOrderState.REJECTED: set(),
}

_COLUMNS = [
    "event_id",
    "order_id",
    "state",
    "event_at_utc",
    "broker_order_id",
    "fill_qty",
    "fill_price",
    "cumulative_fill_qty",
    "average_fill_price",
]
_STRING_COLUMNS = ["event_id", "order_id", "state", "event_at_utc", "broker_order_id"]
_FLOAT_COLUMNS = ["fill_qty", "fill_price", "cumulative_fill_qty", "average_fill_price"]


def _utc_timestamp(value: str) -> pd.Timestamp:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid event_at_utc: {value!r}") from exc
    if timestamp.tzinfo is None:
        raise ValueError("event_at_utc must include a UTC offset")
    return timestamp.tz_convert("UTC")


def _positive_number(value: float | None, name: str) -> float:
    if value is None:
        raise ValueError(f"{name} is required for fill events")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return number


def _event_identifier(payload: dict[str, object], event_id: str | None) -> str:
    if event_id is not None:
        value = str(event_id).strip()
        if not value:
            raise ValueError("event_id must be non-empty")
        return value
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@contextmanager
def _ledger_lock(target: Path) -> Iterator[None]:
    lock_path = target.with_suffix(target.suffix + ".lock")
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read_ledger(target: Path) -> pd.DataFrame:
    if not target.exists():
        empty = pd.DataFrame(columns=_COLUMNS)
        dtypes = {
            **{name: "string" for name in _STRING_COLUMNS},
            **{name: "Float64" for name in _FLOAT_COLUMNS},
        }
        return empty.astype(dtypes)
    ledger = pd.read_parquet(target)
    for column in _COLUMNS:
        if column not in ledger:
            ledger[column] = None
    ledger = ledger[_COLUMNS]
    ledger[_STRING_COLUMNS] = ledger[_STRING_COLUMNS].astype("string")
    ledger[_FLOAT_COLUMNS] = ledger[_FLOAT_COLUMNS].apply(pd.to_numeric, errors="coerce").astype("Float64")
    return ledger


def _same_value(left: object, right: object) -> bool:
    if pd.isna(left) and right is None:
        return True
    if isinstance(right, float):
        try:
            return math.isclose(float(str(left)), right, rel_tol=0.0, abs_tol=1e-12)
        except (TypeError, ValueError):
            return False
    return left == right


def _write_ledger_atomic(target: Path, ledger: pd.DataFrame) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
        ledger.to_parquet(temporary, index=False)
        os.replace(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def record_broker_event(
    ledger_path: str | Path,
    order_id: str,
    state: str,
    *,
    event_at_utc: str,
    event_id: str | None = None,
    broker_order_id: str | None = None,
    fill_qty: float | None = None,
    fill_price: float | None = None,
) -> pd.DataFrame:
    """Atomically append a validated and replay-safe broker event.

    Fill quantities are event deltas.  The ledger records cumulative quantity
    and volume-weighted average price after every event.
    """
    target = Path(ledger_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    order_key = str(order_id).strip()
    if not order_key:
        raise ValueError("order_id must be non-empty")
    event = BrokerOrderState(state)
    timestamp = _utc_timestamp(event_at_utc)
    timestamp_text = timestamp.isoformat().replace("+00:00", "Z")

    is_fill = event in {BrokerOrderState.PARTIALLY_FILLED, BrokerOrderState.FILLED}
    if is_fill:
        event_fill_qty = _positive_number(fill_qty, "fill_qty")
        event_fill_price = _positive_number(fill_price, "fill_price")
    else:
        if fill_qty is not None or fill_price is not None:
            raise ValueError("fill_qty and fill_price are only valid for fill events")
        event_fill_qty = None
        event_fill_price = None

    identity_payload: dict[str, object] = {
        "order_id": order_key,
        "state": event.value,
        "event_at_utc": timestamp_text,
        "broker_order_id": broker_order_id,
        "fill_qty": event_fill_qty,
        "fill_price": event_fill_price,
    }
    identifier = _event_identifier(identity_payload, event_id)

    with _ledger_lock(target):
        previous = _read_ledger(target)
        duplicate = previous.loc[previous["event_id"].astype(str) == identifier]
        if not duplicate.empty:
            row = duplicate.iloc[0]
            if all(_same_value(row[key], value) for key, value in identity_payload.items()):
                return previous
            raise ValueError(f"event_id {identifier!r} was already used with a different payload")

        history = previous.loc[previous["order_id"].astype(str) == order_key]
        if not history.empty:
            prior = BrokerOrderState(str(history.iloc[-1]["state"]))
            if event not in _ALLOWED[prior]:
                raise ValueError(f"illegal broker state transition {prior.value} -> {event.value}")
            prior_timestamp = _utc_timestamp(str(history.iloc[-1]["event_at_utc"]))
            if timestamp <= prior_timestamp:
                raise ValueError("event_at_utc must be strictly increasing for each order")
        elif event is not BrokerOrderState.INTENT:
            raise ValueError("first broker event must be INTENT")

        prior_qty = 0.0
        prior_notional = 0.0
        if not history.empty:
            cumulative = pd.to_numeric(history["cumulative_fill_qty"], errors="coerce")
            if cumulative.notna().any():
                prior_qty = float(cumulative.dropna().iloc[-1])
                average = pd.to_numeric(history["average_fill_price"], errors="coerce").dropna()
                prior_notional = prior_qty * (float(average.iloc[-1]) if not average.empty else 0.0)
            else:
                quantities = pd.to_numeric(history["fill_qty"], errors="coerce").fillna(0.0)
                prices = pd.to_numeric(history["fill_price"], errors="coerce").fillna(0.0)
                prior_qty = float(quantities.sum())
                prior_notional = float((quantities * prices).sum())

        cumulative_qty = prior_qty + (event_fill_qty or 0.0)
        cumulative_notional = prior_notional + (event_fill_qty or 0.0) * (event_fill_price or 0.0)
        average_fill_price = cumulative_notional / cumulative_qty if cumulative_qty > 0 else None
        row = pd.DataFrame(
            [
                {
                    "event_id": identifier,
                    **identity_payload,
                    "cumulative_fill_qty": cumulative_qty,
                    "average_fill_price": average_fill_price,
                }
            ],
            columns=_COLUMNS,
        )
        row[_STRING_COLUMNS] = row[_STRING_COLUMNS].astype("string")
        row[_FLOAT_COLUMNS] = row[_FLOAT_COLUMNS].apply(pd.to_numeric, errors="coerce").astype("Float64")
        result = row if previous.empty else pd.concat([previous, row], ignore_index=True)
        _write_ledger_atomic(target, result)
        return result
