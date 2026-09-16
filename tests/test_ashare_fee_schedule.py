from __future__ import annotations

import pytest

from qlib_platform.backtesting.ashare_costs import execution_fee_breakdown
from qlib_platform.backtesting.ashare_rules import AShareMarketRules, fee_venue


def test_dated_fee_schedule_itemizes_regulatory_and_exchange_charges() -> None:
    rules = AShareMarketRules(commission_bps=0.0, min_commission=0.0)

    before = execution_fee_breakdown(100_000.0, "BUY", rules, trade_date="2023-08-27")
    after = execution_fee_breakdown(100_000.0, "BUY", rules, trade_date="2023-08-28")

    assert before["regulatory_fee"] == pytest.approx(2.0)
    assert after["regulatory_fee"] == pytest.approx(2.0)
    assert before["exchange_handling_fee"] == pytest.approx(4.87)
    assert after["exchange_handling_fee"] == pytest.approx(3.41)
    assert before["total"] == pytest.approx(7.87)
    assert after["total"] == pytest.approx(6.41)


def test_dated_sell_fee_includes_stamp_transfer_regulatory_and_exchange_charges() -> None:
    rules = AShareMarketRules(commission_bps=0.0, min_commission=0.0)

    fees = execution_fee_breakdown(100_000.0, "SELL", rules, trade_date="2023-08-28")

    assert fees["stamp_tax"] == pytest.approx(50.0)
    assert fees["transfer_fee"] == pytest.approx(1.0)
    assert fees["regulatory_fee"] == pytest.approx(2.0)
    assert fees["exchange_handling_fee"] == pytest.approx(3.41)
    assert fees["total"] == pytest.approx(56.41)


def test_bse_fee_schedule_is_venue_and_date_aware() -> None:
    rules = AShareMarketRules(commission_bps=0.0, min_commission=0.0)

    opening = execution_fee_breakdown(
        100_000.0,
        "BUY",
        rules,
        trade_date="2021-11-15",
        venue="BSE",
    )
    first_cut = execution_fee_breakdown(
        100_000.0,
        "BUY",
        rules,
        trade_date="2022-12-01",
        venue="BSE",
    )
    second_cut = execution_fee_breakdown(
        100_000.0,
        "BUY",
        rules,
        trade_date="2023-08-28",
        venue="BSE",
    )

    assert opening["exchange_handling_fee"] == pytest.approx(50.0)
    assert first_cut["exchange_handling_fee"] == pytest.approx(25.0)
    assert second_cut["exchange_handling_fee"] == pytest.approx(12.5)
    assert second_cut["transfer_fee"] == pytest.approx(1.0)
    assert second_cut["fee_regime_id"] == "bse_equity_fee_2023_08_28"
    assert second_cut["fee_venue"] == "BSE"


def test_bse_fee_schedule_fails_closed_before_exchange_launch() -> None:
    rules = AShareMarketRules(commission_bps=0.0, min_commission=0.0)

    with pytest.raises(ValueError, match="BSE.*2021-11-12"):
        execution_fee_breakdown(
            100_000.0,
            "BUY",
            rules,
            trade_date="2021-11-12",
            venue="BSE",
        )


def test_fee_venue_resolves_bse_from_board_or_instrument_suffix() -> None:
    assert fee_venue("920001.BJ") == "BSE"
    assert fee_venue("920001", "BSE") == "BSE"
    assert fee_venue("000001.SZ", "MAIN") == "SSE_SZSE"
