from __future__ import annotations

import pytest

from qlib_platform.backtesting.ashare_costs import execution_fee_breakdown
from qlib_platform.backtesting.ashare_rules import AShareMarketRules


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
