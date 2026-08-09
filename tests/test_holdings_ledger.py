from __future__ import annotations

import pandas as pd
import pytest

from tushare_qlib.holdings_ledger import reconcile_holdings


def _calendar(path):
    pd.DataFrame(
        {"cal_date": pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]), "is_open": [1, 1, 1]}
    ).to_parquet(path)


def test_reconcile_holding_days_and_fail_closed_unknown_position(tmp_path):
    calendar = tmp_path / "calendar.parquet"
    ledger = tmp_path / "ledger.parquet"
    _calendar(calendar)
    positions = pd.DataFrame({"instrument": ["SH600000"], "quantity": [100], "available_quantity": [0]})
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
        positions, None, as_of_date="2026-01-06", calendar_path=calendar, ledger_path=ledger
    )
    assert first.iloc[0]["holding_days"] == 1
    assert second.iloc[0]["holding_days"] == 2

    unknown = pd.DataFrame({"instrument": ["SZ000001"], "quantity": [100], "available_quantity": [100]})
    with pytest.raises(ValueError, match="unexplained broker holding"):
        reconcile_holdings(unknown, None, as_of_date="2026-01-07", calendar_path=calendar, ledger_path=ledger)


def test_reconcile_resets_holding_period_after_full_close_and_reopen(tmp_path):
    calendar = tmp_path / "calendar.parquet"
    ledger = tmp_path / "ledger.parquet"
    _calendar(calendar)
    position = pd.DataFrame({"instrument": ["SH600000"], "quantity": [100], "available_quantity": [0]})
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
        pd.DataFrame(columns=["instrument", "quantity", "available_quantity"]),
        None,
        as_of_date="2026-01-06",
        calendar_path=calendar,
        ledger_path=ledger,
    )
    reopened = reconcile_holdings(
        position,
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
