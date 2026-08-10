from __future__ import annotations

import pandas as pd
import pytest

from tushare_qlib.holdings_ledger import reconcile_holdings


def _calendar(path):
    pd.DataFrame(
        {"cal_date": pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]), "is_open": [1, 1, 1]}
    ).to_parquet(path)


def _snapshot(frame: pd.DataFrame, trade_date: str, *, captured: str | None = None) -> pd.DataFrame:
    return frame.assign(
        as_of_trade_date=trade_date,
        snapshot_at_utc=captured or f"{trade_date}T00:30:00Z",
    )


def test_reconcile_holding_days_and_fail_closed_unknown_position(tmp_path):
    calendar = tmp_path / "calendar.parquet"
    ledger = tmp_path / "ledger.parquet"
    _calendar(calendar)
    positions = _snapshot(
        pd.DataFrame({"instrument": ["SH600000"], "quantity": [100], "available_quantity": [0]}),
        "2026-01-05",
    )
    fills = pd.DataFrame(
        {
            "fill_id": ["fill-1"],
            "trade_date": ["2026-01-05"],
            "instrument": ["SH600000"],
            "side": ["BUY"],
            "quantity": [100],
            "fill_price": [10.0],
        }
    )
    first = reconcile_holdings(
        positions, fills, as_of_date="2026-01-05", calendar_path=calendar, ledger_path=ledger
    )
    second = reconcile_holdings(
        _snapshot(positions.drop(columns=["as_of_trade_date", "snapshot_at_utc"]), "2026-01-06"),
        None,
        as_of_date="2026-01-06",
        calendar_path=calendar,
        ledger_path=ledger,
    )
    assert first.iloc[0]["holding_days"] == 1
    assert second.iloc[0]["holding_days"] == 2

    unknown = _snapshot(
        pd.DataFrame({"instrument": ["SZ000001"], "quantity": [100], "available_quantity": [100]}),
        "2026-01-07",
    )
    with pytest.raises(ValueError, match="unexplained broker holding"):
        reconcile_holdings(unknown, None, as_of_date="2026-01-07", calendar_path=calendar, ledger_path=ledger)


def test_reconcile_resets_holding_period_after_full_close_and_reopen(tmp_path):
    calendar = tmp_path / "calendar.parquet"
    ledger = tmp_path / "ledger.parquet"
    _calendar(calendar)
    position = _snapshot(
        pd.DataFrame({"instrument": ["SH600000"], "quantity": [100], "available_quantity": [0]}),
        "2026-01-05",
    )
    first_fill = pd.DataFrame(
        {
            "fill_id": ["first"],
            "trade_date": ["2026-01-05"],
            "instrument": ["SH600000"],
            "side": ["BUY"],
            "quantity": [100],
            "fill_price": [10.0],
        }
    )
    reconcile_holdings(position, first_fill, as_of_date="2026-01-05", calendar_path=calendar, ledger_path=ledger)
    reconcile_holdings(
        pd.DataFrame(
            columns=[
                "instrument",
                "quantity",
                "available_quantity",
                "as_of_trade_date",
                "snapshot_at_utc",
            ]
        ),
        None,
        as_of_date="2026-01-06",
        calendar_path=calendar,
        ledger_path=ledger,
    )
    reopened = reconcile_holdings(
        _snapshot(position.drop(columns=["as_of_trade_date", "snapshot_at_utc"]), "2026-01-07"),
        pd.DataFrame(
            {
                "fill_id": ["reopen"],
                "trade_date": ["2026-01-07"],
                "instrument": ["SH600000"],
                "side": ["BUY"],
                "quantity": [100],
                "fill_price": [11.0],
            }
        ),
        as_of_date="2026-01-07",
        calendar_path=calendar,
        ledger_path=ledger,
    )
    assert reopened.iloc[0]["opened_trade_date"] == pd.Timestamp("2026-01-07")
    assert reopened.iloc[0]["holding_days"] == 1


def test_reconcile_returns_execution_snapshot_and_keeps_internal_ledger_private(tmp_path):
    calendar = tmp_path / "calendar.parquet"
    ledger = tmp_path / "ledger.parquet"
    _calendar(calendar)
    captured = "2026-01-06T01:02:03Z"
    positions = _snapshot(
        pd.DataFrame(
            {
                "instrument": ["SH600000"],
                "quantity": [200],
                "available_quantity": [100],
                "account_id": ["acct-1"],
                "source": ["paper"],
            }
        ),
        "2026-01-06",
        captured=captured,
    )
    state = reconcile_holdings(
        positions,
        None,
        as_of_date="2026-01-06",
        calendar_path=calendar,
        ledger_path=ledger,
        initial_holdings=pd.DataFrame(
            {"instrument": ["SH600000"], "opened_trade_date": ["2026-01-05"]}
        ),
    )

    assert state.columns.tolist() == [
        "instrument",
        "quantity",
        "available_quantity",
        "holding_days",
        "opened_trade_date",
        "as_of_trade_date",
        "snapshot_at_utc",
        "account_id",
        "source",
    ]
    assert state.iloc[0]["quantity"] == 200
    assert state.iloc[0]["as_of_trade_date"] == "2026-01-06"
    assert state.iloc[0]["snapshot_at_utc"] == pd.Timestamp(captured)
    assert state.iloc[0]["source"] == "paper"
    assert "last_quantity" not in state.columns

    persisted = pd.read_parquet(ledger)
    assert persisted.columns.tolist() == ["instrument", "opened_trade_date", "last_quantity", "as_of_date"]
    assert persisted.iloc[0]["last_quantity"] == 200


def test_reconcile_rejects_snapshot_metadata_mismatch(tmp_path):
    calendar = tmp_path / "calendar.parquet"
    ledger = tmp_path / "ledger.parquet"
    _calendar(calendar)
    positions = _snapshot(
        pd.DataFrame({"instrument": ["SH600000"], "quantity": [100], "available_quantity": [100]}),
        "2026-01-05",
    )

    with pytest.raises(ValueError, match="must match as_of_date"):
        reconcile_holdings(
            positions,
            None,
            as_of_date="2026-01-06",
            calendar_path=calendar,
            ledger_path=ledger,
        )
