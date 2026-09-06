from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from qlib_platform.research.execution import (
    ExecutionModelConfig,
    ParentOrder,
    analyze_broker_events,
    build_pov_schedule,
    build_twap_schedule,
    build_vwap_schedule,
    execution_audit_from_fills,
    execution_benchmarks,
    expected_fill_probability,
    implementation_shortfall,
    normalize_intraday_bars,
    reconcile_simulated_execution,
    simulate_execution,
    simulate_schedule,
)


def _bars(*, rejected_second: bool = False) -> pd.DataFrame:
    start = datetime(2026, 9, 1, 9, 30)
    return pd.DataFrame(
        {
            "timestamp": [start + timedelta(minutes=30 * index) for index in range(4)],
            "instrument": ["000001.SZ"] * 4,
            "close": [10.0, 10.1, 9.9, 10.2],
            "bid": [9.99, 10.09, 9.89, 10.19],
            "ask": [10.01, 10.11, 9.91, 10.21],
            "volume": [100.0, 200.0, 300.0, 400.0],
            "queue_ahead_quantity": [0.0, 0.0, 0.0, 0.0],
            "market_open": [True, True, True, True],
            "rejected": [False, rejected_second, False, False],
        }
    )


def _order(quantity: int = 100, *, side: str = "BUY", decision_price: float | None = None) -> ParentOrder:
    start = datetime(2026, 9, 1, 9, 30)
    return ParentOrder(
        order_id="parent-001",
        instrument="000001.SZ",
        side=side,  # type: ignore[arg-type]
        quantity=quantity,
        start_time=start,
        end_time=start + timedelta(minutes=90),
        decision_price=decision_price,
    )


def test_normalize_intraday_bars_and_duplicate_fail_closed() -> None:
    normalized = normalize_intraday_bars(_bars())
    assert normalized["mid_price"].tolist() == pytest.approx([10.0, 10.1, 9.9, 10.2])
    duplicate = pd.concat([_bars(), _bars().iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="unique"):
        normalize_intraday_bars(duplicate)


def test_execution_benchmarks_are_deterministic() -> None:
    result = execution_benchmarks(_bars(), _order())
    assert result.arrival_price == pytest.approx(10.0)
    assert result.twap == pytest.approx(10.05)
    assert result.vwap == pytest.approx(10.07)
    assert result.end_price == pytest.approx(10.2)
    assert result.total_volume == pytest.approx(1_000.0)


def test_twap_integer_schedule_conserves_parent_quantity() -> None:
    schedule = build_twap_schedule(_order(101), _bars())
    assert schedule["target_quantity"].tolist() == [26, 25, 25, 25]
    assert int(schedule["target_quantity"].sum()) == 101
    assert int(schedule.iloc[-1]["remaining_quantity_after"]) == 0


def test_vwap_schedule_matches_realized_volume_profile() -> None:
    schedule = build_vwap_schedule(_order(100), _bars())
    assert schedule["target_quantity"].tolist() == [10, 20, 30, 40]
    assert int(schedule["target_quantity"].sum()) == 100


def test_pov_schedule_respects_participation_capacity_and_remainder() -> None:
    schedule = build_pov_schedule(_order(500), _bars(), participation_rate=0.05)
    assert schedule["target_quantity"].tolist() == [5, 10, 15, 20]
    assert int(schedule["target_quantity"].sum()) == 50
    assert int(schedule.iloc[-1]["remaining_quantity_after"]) == 450


def test_queue_ahead_reduces_expected_fill_probability() -> None:
    config = ExecutionModelConfig(max_participation_rate=0.10, queue_liquidity_fraction=1.0)
    no_queue = expected_fill_probability(100, 1_000.0, 0.0, config)
    queued = expected_fill_probability(100, 1_000.0, 50.0, config)
    assert no_queue == pytest.approx(1.0)
    assert queued == pytest.approx(0.5)


def test_simulator_produces_partial_fill_under_capacity() -> None:
    order = _order(200)
    schedule = build_twap_schedule(order, _bars().iloc[[0]].copy())
    result = simulate_schedule(
        order,
        _bars().iloc[[0]].copy(),
        schedule,
        config=ExecutionModelConfig(max_participation_rate=0.10),
    )
    fill = result.fills.iloc[0]
    assert int(fill["filled_quantity"]) == 10
    assert fill["status"] == "PARTIAL"
    assert bool(fill["partial_fill"])


def test_buy_and_sell_impact_move_prices_in_adverse_directions() -> None:
    config = ExecutionModelConfig(max_participation_rate=1.0, impact_coefficient_bps=20.0)
    buy = simulate_execution(_order(20, side="BUY"), _bars(), strategy="twap", config=config)
    sell = simulate_execution(_order(20, side="SELL"), _bars(), strategy="twap", config=config)
    buy_fill = buy.fills.loc[buy.fills["filled_quantity"] > 0].iloc[0]
    sell_fill = sell.fills.loc[sell.fills["filled_quantity"] > 0].iloc[0]
    assert float(buy_fill["fill_price"]) > float(buy_fill["reference_price"])
    assert float(sell_fill["fill_price"]) < float(sell_fill["reference_price"])
    assert float(buy_fill["slippage_bps"]) > 0
    assert float(sell_fill["slippage_bps"]) > 0


def test_latency_penalty_increases_adverse_slippage() -> None:
    order = _order(20)
    baseline = simulate_execution(
        order,
        _bars(),
        config=ExecutionModelConfig(max_participation_rate=1.0, latency_ms=0, latency_bps_per_second=5.0),
    )
    delayed = simulate_execution(
        order,
        _bars(),
        config=ExecutionModelConfig(max_participation_rate=1.0, latency_ms=2_000, latency_bps_per_second=5.0),
    )
    assert float(delayed.summary["weighted_slippage_bps"]) > float(
        baseline.summary["weighted_slippage_bps"]
    )


def test_canceled_and_rejected_children_do_not_fill() -> None:
    order = _order(100)
    bars = _bars(rejected_second=True).iloc[:2].copy()
    schedule = build_twap_schedule(order, bars)
    schedule["canceled"] = [True, False]
    result = simulate_schedule(order, bars, schedule)
    assert result.fills["status"].tolist() == ["CANCELED", "REJECTED"]
    assert int(result.fills["filled_quantity"].sum()) == 0


def test_implementation_shortfall_components_reconcile() -> None:
    order = _order(40, decision_price=9.95)
    simulation = simulate_execution(
        order,
        _bars(),
        config=ExecutionModelConfig(max_participation_rate=1.0, fee_bps=2.0),
    )
    result = implementation_shortfall(order, simulation)
    assert result.filled_quantity == 40
    assert result.unfilled_quantity == 0
    assert result.total_cost == pytest.approx(
        result.delay_cost + result.execution_cost + result.opportunity_cost + result.fees
    )
    assert result.total_shortfall_bps > 0


def test_broker_event_analysis_reports_latency_reject_cancel_and_partial_fill() -> None:
    start = datetime(2026, 9, 1, 9, 30)
    events = pd.DataFrame(
        {
            "order_id": ["a", "b", "c"],
            "status": ["FILLED", "REJECTED", "CANCELED"],
            "requested_quantity": [100, 100, 100],
            "filled_quantity": [100, 0, 40],
            "submitted_at": [start, start, start],
            "ack_at": [
                start + timedelta(milliseconds=10),
                start + timedelta(milliseconds=20),
                start + timedelta(milliseconds=30),
            ],
            "side": ["BUY", "BUY", "SELL"],
            "reference_price": [10.0, 10.0, 10.0],
            "fill_price": [10.01, None, 9.99],
        }
    )
    result = analyze_broker_events(events)
    assert result["reject_count"] == 1
    assert result["cancel_count"] == 1
    assert result["partial_fill_count"] == 1
    assert result["fill_ratio"] == pytest.approx(140 / 300)
    assert result["ack_latency_p50_ms"] == pytest.approx(20.0)
    assert result["ack_latency_p95_ms"] == pytest.approx(29.0)
    assert result["mean_signed_slippage_bps"] == pytest.approx(10.0)


def test_reconciliation_adapter_reuses_existing_execution_audit() -> None:
    order = _order(40)
    simulation = simulate_execution(
        order,
        _bars(),
        config=ExecutionModelConfig(max_participation_rate=1.0, fee_bps=1.0),
    )
    audit = execution_audit_from_fills(simulation.fills)
    turnover = float(audit["filled_value"].sum())
    cost = float(audit["trade_cost"].sum())
    report = pd.DataFrame(
        {"total_turnover": [turnover], "total_cost": [cost]},
        index=[pd.Timestamp("2026-09-01")],
    )
    daily, result = reconcile_simulated_execution(simulation.fills, report)
    assert result.passed
    assert bool(daily.iloc[0]["passed"])


def test_reconciliation_adapter_rejects_negative_inventory() -> None:
    order = _order(20, side="SELL")
    simulation = simulate_execution(
        order,
        _bars(),
        config=ExecutionModelConfig(max_participation_rate=1.0),
    )
    with pytest.raises(ValueError, match="negative inventory"):
        execution_audit_from_fills(simulation.fills)
