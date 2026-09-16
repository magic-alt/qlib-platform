from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from qlib_platform.backtesting.ashare_costs import execution_fee_breakdown
from qlib_platform.backtesting.ashare_rules import AShareMarketRules, infer_price_limit_pct
from qlib_platform.backtesting.ashare_simulator import simulate_ashare_orders
from qlib_platform.backtesting.market_rule_set import (
    execution_contract_from_mapping,
    production_realism_rule_set,
)
from qlib_platform.research.workflow.experiment import ResearchExperimentSpec


def _bar(
    date: str,
    *,
    instrument: str = "000001.SZ",
    open_: float | None = 10.0,
    close: float = 10.0,
    prev_close: float = 10.0,
    volume: int = 100_000,
    **extra: object,
) -> dict[str, object]:
    return {
        "trade_date": date,
        "instrument": instrument,
        "open": open_,
        "close": close,
        "prev_close": prev_close,
        "volume": volume,
        **extra,
    }


def test_production_rules_have_unique_version_source_and_fixture() -> None:
    rule_set = production_realism_rule_set()
    ids = [rule.rule_id for rule in rule_set.rules]
    fee_ids = [regime.regime_id for regime in rule_set.fee_regimes]

    assert len(ids) == len(set(ids))
    assert len(fee_ids) == len(set(fee_ids))
    assert all(rule.source and rule.description and rule.fixture for rule in rule_set.rules)
    assert all(regime.source and regime.fixture for regime in rule_set.fee_regimes)
    assert rule_set.fingerprint


def test_versioned_fee_boundaries() -> None:
    rules = AShareMarketRules(commission_bps=0.0, min_commission=0.0)

    before_transfer_cut = execution_fee_breakdown(100_000.0, "SELL", rules, trade_date="2022-04-28")
    after_transfer_cut = execution_fee_breakdown(100_000.0, "SELL", rules, trade_date="2022-04-29")
    before_stamp_cut = execution_fee_breakdown(100_000.0, "SELL", rules, trade_date="2023-08-27")
    after_stamp_cut = execution_fee_breakdown(100_000.0, "SELL", rules, trade_date="2023-08-28")

    assert before_transfer_cut["transfer_fee"] == pytest.approx(2.0)
    assert after_transfer_cut["transfer_fee"] == pytest.approx(1.0)
    assert before_stamp_cut["stamp_tax"] == pytest.approx(100.0)
    assert after_stamp_cut["stamp_tax"] == pytest.approx(50.0)
    assert after_stamp_cut["fee_regime_id"] == "cn_equity_fee_2023_08_28"


def test_price_limit_regime_boundaries() -> None:
    rules = AShareMarketRules()

    assert (
        infer_price_limit_pct(
            board="CHINEXT", is_st=False, listing_days=20, rules=rules, trade_date="2020-08-21"
        )
        == 0.10
    )
    assert (
        infer_price_limit_pct(
            board="CHINEXT", is_st=False, listing_days=20, rules=rules, trade_date="2020-08-24"
        )
        == 0.20
    )
    assert (
        infer_price_limit_pct(board="MAIN", is_st=True, listing_days=20, rules=rules, trade_date="2026-07-03")
        == 0.05
    )
    assert (
        infer_price_limit_pct(board="MAIN", is_st=True, listing_days=20, rules=rules, trade_date="2026-07-06")
        == 0.10
    )
    assert (
        infer_price_limit_pct(board="MAIN", is_st=False, listing_days=0, rules=rules, trade_date="2023-04-10")
        is None
    )
    assert infer_price_limit_pct(board="BSE", is_st=False, listing_days=0, rules=rules) is None
    assert infer_price_limit_pct(board="BSE", is_st=False, listing_days=1, rules=rules) == 0.30
    with pytest.raises(ValueError, match="asymmetric"):
        infer_price_limit_pct(board="MAIN", is_st=False, listing_days=0, rules=rules, trade_date="2022-01-04")


def test_t_plus_one_and_sellable_quantity_ledger() -> None:
    bars = pd.DataFrame(
        [
            _bar("2026-01-05"),
            _bar("2026-01-06"),
        ]
    )
    orders = pd.DataFrame(
        [
            {
                "order_id": "buy",
                "trade_date": "2026-01-05",
                "instrument": "000001.SZ",
                "side": "BUY",
                "quantity": 200,
            },
            {
                "order_id": "same-day-sell",
                "trade_date": "2026-01-05",
                "instrument": "000001.SZ",
                "side": "SELL",
                "quantity": 100,
            },
            {
                "order_id": "next-day-sell",
                "trade_date": "2026-01-06",
                "instrument": "000001.SZ",
                "side": "SELL",
                "quantity": 100,
            },
        ]
    )

    result = simulate_ashare_orders(bars, orders, initial_cash=100_000)

    assert list(result.fills["order_id"]) == ["buy", "next-day-sell"]
    assert result.rejections.iloc[0]["reason"] == "t_plus_one_or_no_position"
    first_day = result.daily_positions.loc[
        result.daily_positions["trade_date"] == pd.Timestamp("2026-01-05")
    ].iloc[0]
    second_day = result.daily_positions.loc[
        result.daily_positions["trade_date"] == pd.Timestamp("2026-01-06")
    ].iloc[0]
    assert int(first_day["quantity"]) == 200
    assert int(first_day["available_quantity"]) == 0
    assert int(second_day["quantity"]) == 100
    assert int(second_day["available_quantity"]) == 100


def test_board_lot_and_odd_lot_sell_semantics() -> None:
    bars = pd.DataFrame([_bar("2026-01-05"), _bar("2026-01-06"), _bar("2026-01-07")])
    orders = pd.DataFrame(
        [
            {
                "trade_date": "2026-01-05",
                "instrument": "000001.SZ",
                "side": "BUY",
                "quantity": 100,
            },
            {
                "trade_date": "2026-01-06",
                "instrument": "000001.SZ",
                "side": "SELL",
                "quantity": 100,
            },
            {
                "trade_date": "2026-01-07",
                "instrument": "000001.SZ",
                "side": "SELL",
                "quantity": 50,
            },
        ]
    )
    actions = pd.DataFrame(
        [
            {
                "event_id": "bonus",
                "effective_date": "2026-01-06",
                "instrument": "000001.SZ",
                "event_type": "SHARE_MULTIPLIER",
                "share_multiplier": 1.5,
            }
        ]
    )

    result = simulate_ashare_orders(bars, orders, initial_cash=100_000, corporate_actions=actions)

    assert list(result.fills["filled_quantity"].astype(int)) == [100, 100, 50]
    assert result.positions.empty


def test_star_odd_lot_tail_is_sold_in_full() -> None:
    instrument = "688981.SH"
    bars = pd.DataFrame(
        [
            _bar(
                "2026-01-05",
                instrument=instrument,
                open_=50.0,
                close=50.0,
                prev_close=49.0,
                board="STAR",
            ),
            _bar(
                "2026-01-06",
                instrument=instrument,
                open_=50.0,
                close=50.0,
                prev_close=50.0,
                board="STAR",
            ),
            _bar(
                "2026-01-07",
                instrument=instrument,
                open_=50.0,
                close=50.0,
                prev_close=50.0,
                board="STAR",
            ),
        ]
    )
    orders = pd.DataFrame(
        [
            {
                "trade_date": "2026-01-05",
                "instrument": instrument,
                "side": "BUY",
                "quantity": 399,
            },
            {
                "trade_date": "2026-01-06",
                "instrument": instrument,
                "side": "SELL",
                "quantity": 200,
            },
            {
                "trade_date": "2026-01-07",
                "instrument": instrument,
                "side": "SELL",
                "quantity": 199,
            },
        ]
    )

    result = simulate_ashare_orders(bars, orders, initial_cash=100_000)

    assert list(result.fills["filled_quantity"].astype(int)) == [399, 200, 199]
    assert result.positions.empty


def test_limit_fillability_is_directional() -> None:
    up_bars = pd.DataFrame(
        [
            _bar("2026-01-05"),
            _bar("2026-01-06", open_=11.0, close=11.0, prev_close=10.0, is_limit_up=True),
        ]
    )
    up_orders = pd.DataFrame(
        [
            {
                "order_id": "seed",
                "trade_date": "2026-01-05",
                "instrument": "000001.SZ",
                "side": "BUY",
                "quantity": 200,
            },
            {
                "order_id": "buy-up",
                "trade_date": "2026-01-06",
                "instrument": "000001.SZ",
                "side": "BUY",
                "quantity": 100,
            },
            {
                "order_id": "sell-up",
                "trade_date": "2026-01-06",
                "instrument": "000001.SZ",
                "side": "SELL",
                "quantity": 100,
            },
        ]
    )
    up = simulate_ashare_orders(up_bars, up_orders, initial_cash=100_000)
    assert "buy-up" not in set(up.fills["order_id"])
    assert "sell-up" in set(up.fills["order_id"])
    assert "limit_up_no_buy_liquidity" in set(up.rejections["reason"])

    down_bars = pd.DataFrame(
        [
            _bar("2026-01-05"),
            _bar("2026-01-06", open_=9.0, close=9.0, prev_close=10.0, is_limit_down=True),
        ]
    )
    down_orders = pd.DataFrame(
        [
            {
                "order_id": "seed",
                "trade_date": "2026-01-05",
                "instrument": "000001.SZ",
                "side": "BUY",
                "quantity": 200,
            },
            {
                "order_id": "buy-down",
                "trade_date": "2026-01-06",
                "instrument": "000001.SZ",
                "side": "BUY",
                "quantity": 100,
            },
            {
                "order_id": "sell-down",
                "trade_date": "2026-01-06",
                "instrument": "000001.SZ",
                "side": "SELL",
                "quantity": 100,
            },
        ]
    )
    down = simulate_ashare_orders(down_bars, down_orders, initial_cash=100_000)
    assert "buy-down" in set(down.fills["order_id"])
    assert "sell-down" not in set(down.fills["order_id"])
    assert "limit_down_no_sell_liquidity" in set(down.rejections["reason"])


def test_suspension_and_missing_bar_are_distinct() -> None:
    bars = pd.DataFrame(
        [
            _bar("2026-01-05"),
            _bar("2026-01-06", open_=None, close=10.0, volume=0, paused=True),
            _bar("2026-01-07", open_=None, close=10.0, volume=0, trading_status="SUSPENDED"),
            _bar("2026-01-08"),
        ]
    )
    orders = pd.DataFrame(
        [
            {
                "trade_date": "2026-01-05",
                "instrument": "000001.SZ",
                "side": "BUY",
                "quantity": 100,
            },
            {
                "trade_date": "2026-01-06",
                "instrument": "000001.SZ",
                "side": "SELL",
                "quantity": 100,
            },
            {
                "trade_date": "2026-01-07",
                "instrument": "000001.SZ",
                "side": "SELL",
                "quantity": 100,
            },
            {
                "trade_date": "2026-01-07",
                "instrument": "000002.SZ",
                "side": "BUY",
                "quantity": 100,
            },
            {
                "trade_date": "2026-01-08",
                "instrument": "000001.SZ",
                "side": "SELL",
                "quantity": 100,
            },
        ]
    )

    result = simulate_ashare_orders(bars, orders, initial_cash=100_000)

    assert list(result.rejections["reason"]).count("suspended_or_zero_volume") == 2
    assert "missing_market_data" in set(result.rejections["reason"])
    assert list(result.fills["side"]) == ["BUY", "SELL"]


def test_corporate_action_nav_continuity_and_adjusted_price_guard() -> None:
    bars = pd.DataFrame(
        [
            _bar("2026-01-05", open_=10.0, close=10.0, prev_close=9.9),
            _bar("2026-01-06", open_=9.5, close=9.5, prev_close=10.0),
        ]
    )
    orders = pd.DataFrame(
        [
            {
                "trade_date": "2026-01-05",
                "instrument": "000001.SZ",
                "side": "BUY",
                "quantity": 1000,
            }
        ]
    )
    actions = pd.DataFrame(
        [
            {
                "event_id": "cash-dividend",
                "effective_date": "2026-01-06",
                "instrument": "000001.SZ",
                "event_type": "CASH_DIVIDEND",
                "cash_per_share": 0.5,
            }
        ]
    )

    result = simulate_ashare_orders(bars, orders, initial_cash=100_000, corporate_actions=actions)

    assert len(result.corporate_actions) == 1
    assert float(result.corporate_actions.iloc[0]["cash_delta"]) == pytest.approx(500.0)
    assert float(result.daily_account.iloc[0]["equity"]) == pytest.approx(
        float(result.daily_account.iloc[1]["equity"])
    )
    with pytest.raises(ValueError, match="double count"):
        simulate_ashare_orders(
            bars,
            orders,
            initial_cash=100_000,
            corporate_actions=actions,
            rules=AShareMarketRules(price_basis="adjusted"),
        )


def test_listing_lifecycle_rejects_prelisting_and_delisted_rows() -> None:
    bars = pd.DataFrame(
        [
            _bar("2026-01-05", instrument="000001.SZ", listed=False),
            _bar("2026-01-05", instrument="000002.SZ", trading_status="DELISTED"),
        ]
    )
    orders = pd.DataFrame(
        [
            {
                "trade_date": "2026-01-05",
                "instrument": "000001.SZ",
                "side": "BUY",
                "quantity": 100,
            },
            {
                "trade_date": "2026-01-05",
                "instrument": "000002.SZ",
                "side": "BUY",
                "quantity": 100,
            },
        ]
    )

    result = simulate_ashare_orders(bars, orders, initial_cash=100_000)

    assert result.fills.empty
    assert set(result.rejections["reason"]) == {"not_listed", "delisted"}


def test_market_rule_change_changes_research_experiment_identity() -> None:
    base = ResearchExperimentSpec(
        data_release_id="ds_fixture",
        alpha_pack_id="alpha158_market_v1",
        alpha_pack_sha256="alpha-sha",
        feature_set_id="alpha158_market_v1",
        feature_set_sha256="feature-sha",
        label_spec_id="label_v1",
        label={"horizon": 1},
        split_profile_id="fixed_split_v1",
        split_sha256="split-sha",
        model_profile_id="lightgbm_cpu",
        model_profile_sha256="model-sha",
        portfolio_policy_id="topk_dropout_v1",
        portfolio_policy_sha256="portfolio-sha",
        benchmark="SH000300",
        market_rule_set_id="rules-v1",
        market_rule_set_sha256="rules-sha-v1",
        cost_model_id="cost-v1",
        fill_model_id="fill-v1",
        execution_contract_sha256="exec-sha-v1",
    )
    changed = replace(
        base,
        market_rule_set_id="rules-v2",
        market_rule_set_sha256="rules-sha-v2",
        execution_contract_sha256="exec-sha-v2",
    )

    assert base.experiment_id != changed.experiment_id


def test_official_parity_and_production_realism_contracts_are_explicitly_distinct() -> None:
    official = execution_contract_from_mapping({})
    realism = execution_contract_from_mapping({"market_rule_profile": "production_realism_v1"})

    assert official["marketRuleSetId"] == "qlib_official_parity_v1"
    assert realism["marketRuleSetId"] == "cn_cash_equity_production_realism_v1"
    assert official["executionContractSha256"] != realism["executionContractSha256"]
