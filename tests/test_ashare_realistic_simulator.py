from __future__ import annotations

import pandas as pd

from qlib_platform.backtesting.ashare_costs import execution_fees
from qlib_platform.backtesting.ashare_rules import normalize_buy_quantity
from qlib_platform.backtesting.ashare_simulator import (
    AShareMarketRules,
    infer_price_limit_pct,
    simulate_ashare_orders,
)


def _bars() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "trade_date": "2026-01-05",
                "instrument": "000001.SZ",
                "open": 10.0,
                "close": 10.1,
                "prev_close": 9.9,
                "volume": 100_000,
            },
            {
                "trade_date": "2026-01-06",
                "instrument": "000001.SZ",
                "open": 10.2,
                "close": 10.3,
                "prev_close": 10.1,
                "volume": 100_000,
            },
            {
                "trade_date": "2026-01-07",
                "instrument": "000001.SZ",
                "open": 10.3,
                "close": 10.2,
                "prev_close": 10.3,
                "volume": 100_000,
            },
        ]
    )


def test_t_plus_one_blocks_same_day_sale_and_releases_next_session() -> None:
    orders = pd.DataFrame(
        [
            {"trade_date": "2026-01-05", "instrument": "000001.SZ", "side": "BUY", "quantity": 1000},
            {"trade_date": "2026-01-05", "instrument": "000001.SZ", "side": "SELL", "quantity": 500},
            {"trade_date": "2026-01-06", "instrument": "000001.SZ", "side": "SELL", "quantity": 500},
        ]
    )
    result = simulate_ashare_orders(_bars(), orders, initial_cash=100_000)
    assert list(result.fills["side"]) == ["BUY", "SELL"]
    assert "t_plus_one_or_no_position" in set(result.rejections["reason"])
    assert int(result.positions.iloc[0]["quantity"]) == 500


def test_default_fee_profile_matches_wanyi_mianwu_cash_account() -> None:
    rules = AShareMarketRules()

    assert rules.commission_bps == 1.0
    assert rules.min_commission == 0.0
    assert execution_fees(100_000.0, "BUY", rules) == 11.0
    assert execution_fees(100_000.0, "SELL", rules) == 61.0


def test_board_specific_buy_quantity_rules() -> None:
    rules = AShareMarketRules()

    assert normalize_buy_quantity("000001.SZ", 356, rules) == 300
    assert normalize_buy_quantity("SH688981", 199, rules) == 0
    assert normalize_buy_quantity("SH688981", 399, rules) == 399
    assert normalize_buy_quantity("688981.SH", 401, rules) == 401


def test_star_market_simulator_accepts_one_share_increments_above_minimum() -> None:
    bars = pd.DataFrame(
        [
            {
                "trade_date": "2026-01-05",
                "instrument": "688981.SH",
                "open": 50.0,
                "close": 50.5,
                "prev_close": 49.0,
                "volume": 100_000,
                "board": "STAR",
            }
        ]
    )
    orders = pd.DataFrame(
        [{"trade_date": "2026-01-05", "instrument": "688981.SH", "side": "BUY", "quantity": 399}]
    )

    result = simulate_ashare_orders(bars, orders, initial_cash=100_000)

    assert int(result.fills.iloc[0]["filled_quantity"]) == 399


def test_volume_participation_creates_partial_fill() -> None:
    bars = _bars().copy()
    bars.loc[0, "volume"] = 2_000
    orders = pd.DataFrame(
        [{"trade_date": "2026-01-05", "instrument": "000001.SZ", "side": "BUY", "quantity": 1000}]
    )
    result = simulate_ashare_orders(
        bars,
        orders,
        initial_cash=100_000,
        rules=AShareMarketRules(max_participation_rate=0.10),
    )
    fill = result.fills.iloc[0]
    assert int(fill["filled_quantity"]) == 200
    assert bool(fill["partial_fill"])
    assert abs(float(fill["participation_rate"]) - 0.10) < 1e-12


def test_limit_up_buy_is_rejected_fail_closed() -> None:
    bars = _bars().copy()
    bars.loc[0, "is_limit_up"] = True
    orders = pd.DataFrame(
        [{"trade_date": "2026-01-05", "instrument": "000001.SZ", "side": "BUY", "quantity": 100}]
    )
    result = simulate_ashare_orders(bars, orders)
    assert result.fills.empty
    assert result.rejections.iloc[0]["reason"] == "limit_up_no_buy_liquidity"


def test_limit_inference_covers_st_growth_beijing_and_ipo() -> None:
    rules = AShareMarketRules()
    assert infer_price_limit_pct(board="MAIN", is_st=True, listing_days=20, rules=rules) == 0.05
    assert infer_price_limit_pct(board="CHINEXT", is_st=True, listing_days=20, rules=rules) == 0.20
    assert infer_price_limit_pct(board="BSE", is_st=False, listing_days=20, rules=rules) == 0.30
    assert infer_price_limit_pct(board="STAR", is_st=False, listing_days=2, rules=rules) is None


def test_spread_slippage_impact_and_capacity_are_reported() -> None:
    orders = pd.DataFrame(
        [{"trade_date": "2026-01-05", "instrument": "000001.SZ", "side": "BUY", "quantity": 1000}]
    )
    result = simulate_ashare_orders(_bars(), orders, initial_cash=100_000)
    fill = result.fills.iloc[0]
    assert float(fill["fill_price"]) > float(fill["reference_price"])
    assert float(fill["impact_bps"]) > 0
    assert float(fill["capacity_notional"]) > 0
    assert result.summary["capacity_utilization"] > 0


def test_daily_participation_capacity_is_shared_across_multiple_orders() -> None:
    bars = _bars().copy()
    bars.loc[0, "volume"] = 2_000
    orders = pd.DataFrame(
        [
            {
                "order_id": "a",
                "trade_date": "2026-01-05",
                "instrument": "000001.SZ",
                "side": "BUY",
                "quantity": 200,
            },
            {
                "order_id": "b",
                "trade_date": "2026-01-05",
                "instrument": "000001.SZ",
                "side": "BUY",
                "quantity": 200,
            },
        ]
    )
    result = simulate_ashare_orders(
        bars,
        orders,
        initial_cash=100_000,
        rules=AShareMarketRules(max_participation_rate=0.10),
    )
    assert int(result.fills["filled_quantity"].sum()) == 200
    assert "volume_below_buy_lot" in set(result.rejections["reason"])
