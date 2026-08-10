from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import pytest

from tushare_qlib.broker_state import record_broker_event


def test_broker_event_replay_is_idempotent_and_event_id_collision_fails(tmp_path: Path):
    ledger = tmp_path / "events.parquet"
    first = record_broker_event(
        ledger,
        "order-1",
        "INTENT",
        event_id="event-1",
        event_at_utc="2026-08-10T10:00:00+09:00",
    )
    replay = record_broker_event(
        ledger,
        "order-1",
        "INTENT",
        event_id="event-1",
        event_at_utc="2026-08-10T10:00:00+09:00",
    )

    assert len(first) == len(replay) == 1
    assert replay.iloc[0]["event_at_utc"] == "2026-08-10T01:00:00Z"
    with pytest.raises(ValueError, match="different payload"):
        record_broker_event(
            ledger,
            "order-2",
            "INTENT",
            event_id="event-1",
            event_at_utc="2026-08-10T01:00:01Z",
        )


def test_broker_event_rejects_naive_and_backwards_timestamps(tmp_path: Path):
    ledger = tmp_path / "events.parquet"
    with pytest.raises(ValueError, match="UTC offset"):
        record_broker_event(ledger, "order-1", "INTENT", event_at_utc="2026-08-10 01:00:00")

    record_broker_event(ledger, "order-1", "INTENT", event_at_utc="2026-08-10T01:00:00Z")
    with pytest.raises(ValueError, match="strictly increasing"):
        record_broker_event(ledger, "order-1", "SUBMITTED", event_at_utc="2026-08-10T00:59:59Z")


def test_partial_fills_accumulate_quantity_and_volume_weighted_price(tmp_path: Path):
    ledger = tmp_path / "events.parquet"
    record_broker_event(ledger, "order-1", "INTENT", event_at_utc="2026-08-10T01:00:00Z")
    record_broker_event(ledger, "order-1", "SUBMITTED", event_at_utc="2026-08-10T01:01:00Z")
    partial = record_broker_event(
        ledger,
        "order-1",
        "PARTIALLY_FILLED",
        event_at_utc="2026-08-10T01:02:00Z",
        fill_qty=40,
        fill_price=10,
    )
    filled = record_broker_event(
        ledger,
        "order-1",
        "FILLED",
        event_at_utc="2026-08-10T01:03:00Z",
        fill_qty=60,
        fill_price=11,
    )

    assert partial.iloc[-1]["cumulative_fill_qty"] == 40
    assert partial.iloc[-1]["average_fill_price"] == 10
    assert filled.iloc[-1]["cumulative_fill_qty"] == 100
    assert filled.iloc[-1]["average_fill_price"] == pytest.approx(10.6)
    with pytest.raises(ValueError, match="required for fill"):
        record_broker_event(
            tmp_path / "missing-fill.parquet",
            "order-2",
            "PARTIALLY_FILLED",
            event_at_utc="2026-08-10T01:00:00Z",
        )


def test_concurrent_broker_events_do_not_lose_updates(tmp_path: Path):
    ledger = tmp_path / "events.parquet"

    def append(index: int) -> None:
        record_broker_event(
            ledger,
            f"order-{index}",
            "INTENT",
            event_id=f"event-{index}",
            event_at_utc="2026-08-10T01:00:00Z",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append, range(24)))

    result = pd.read_parquet(ledger)
    assert len(result) == 24
    assert result["event_id"].nunique() == 24
    assert not list(tmp_path.glob(".events.parquet.*.tmp"))
