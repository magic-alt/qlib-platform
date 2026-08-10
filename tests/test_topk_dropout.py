from __future__ import annotations

import pandas as pd
import pytest

from tushare_qlib.artifacts import ArtifactType
from tushare_qlib.execution import ExecutionPolicy, build_topk_orders
from tushare_qlib.holdings_ledger import reconcile_holdings
from tushare_qlib.topk_dropout import TopkDropoutPolicy, topk_dropout_decision


def _quotes(rows: list[str], *, limited_up: set[str] | None = None) -> pd.DataFrame:
    limited_up = limited_up or set()
    return pd.DataFrame(
        {
            "instrument": rows,
            "price": [10.0] * len(rows),
            "paused": [0] * len(rows),
            "is_limit_up": [int(code in limited_up) for code in rows],
            "is_limit_down": [0] * len(rows),
            "adv20_volume": [100_000] * len(rows),
            "sector": [f"sector-{index}" for index in range(len(rows))],
        }
    )


def test_topk_dropout_compares_combined_holdings_and_candidates():
    scores = pd.Series({"A": 0.9, "B": 0.8, "C": 0.7, "D": 0.6})
    positions = pd.DataFrame({"instrument": ["B", "D"], "quantity": [100, 100], "holding_days": [5, 5]})
    decision = topk_dropout_decision(
        scores,
        positions,
        _quotes(list(scores.index)),
        policy=TopkDropoutPolicy(topk=2, n_drop=1, hold_thresh=5),
    )
    action = decision.set_index("instrument")["target_action"].to_dict()
    assert action["D"] == "SELL"
    assert action["A"] == "BUY"
    assert action["B"] == "HOLD"


def test_hold_threshold_blocks_sell_without_removing_qib_buy_intent():
    scores = pd.Series({"A": 0.9, "B": 0.8, "C": 0.7, "D": 0.6})
    positions = pd.DataFrame({"instrument": ["B", "D"], "quantity": [100, 100], "holding_days": [5, 3]})
    decision = topk_dropout_decision(
        scores,
        positions,
        _quotes(list(scores.index)),
        policy=TopkDropoutPolicy(topk=2, n_drop=1, hold_thresh=5),
    ).set_index("instrument")
    assert decision.at["D", "target_action"] == "HOLD"
    assert decision.at["D", "action_reason"] == "HOLD_THRESHOLD"
    assert decision.at["A", "target_action"] == "BUY"


def test_only_tradable_skips_limited_candidate_before_combined_ranking():
    scores = pd.Series({"A": 0.9, "B": 0.8, "C": 0.7, "D": 0.6})
    positions = pd.DataFrame({"instrument": ["B", "D"], "quantity": [100, 100], "holding_days": [5, 5]})
    decision = topk_dropout_decision(
        scores,
        positions,
        _quotes(list(scores.index), limited_up={"A"}),
        policy=TopkDropoutPolicy(topk=2, n_drop=1, hold_thresh=5),
    ).set_index("instrument")
    assert decision.at["D", "target_action"] == "SELL"
    assert decision.at["C", "target_action"] == "BUY"
    assert not decision.at["A", "is_buy_candidate"]
    assert decision.at["A", "target_action"] == "HOLD"


def test_topk_order_builder_keeps_buy_when_t1_blocks_requested_sell(governed_artifact):
    scores = pd.Series({"A": 0.9, "B": 0.8, "C": 0.7, "D": 0.6})
    score_artifact = governed_artifact(
        scores.rename("score")
        .rename_axis("instrument")
        .reset_index()
        .assign(signal_date="2026-01-05", trade_date="2026-01-06"),
        ArtifactType.MODEL_SCORE,
    )
    positions = pd.DataFrame(
        {
            "instrument": ["B", "D"],
            "quantity": [1000, 1000],
            "available_quantity": [1000, 0],
            "holding_days": [5, 5],
        }
    ).assign(as_of_trade_date="2026-01-06", snapshot_at_utc=pd.Timestamp.now(tz="UTC").isoformat())
    quotes = _quotes(list(scores.index)).assign(
        as_of_trade_date="2026-01-06", snapshot_at_utc=pd.Timestamp.now(tz="UTC").isoformat()
    )
    decision, orders, blocked = build_topk_orders(
        score_artifact,
        positions,
        quotes,
        signal_date="2026-01-05",
        trade_date="2026-01-06",
        cash=10_000,
        strategy_policy=TopkDropoutPolicy(topk=2, n_drop=1, hold_thresh=5),
        execution_policy=ExecutionPolicy(),
        daily_pnl_pct=0.0,
    )
    assert decision.set_index("instrument").at["D", "target_action"] == "SELL"
    assert orders["side"].tolist() == ["BUY"]
    assert orders.iloc[0]["instrument"] == "A"
    assert blocked.iloc[0]["reason"] == "T1_NOT_SELLABLE"


def test_reconciled_snapshot_flows_directly_into_topk(tmp_path, governed_artifact):
    captured = pd.Timestamp.now(tz="UTC").isoformat()
    calendar = tmp_path / "calendar.parquet"
    pd.DataFrame({"cal_date": pd.to_datetime(["2026-01-05", "2026-01-06"]), "is_open": [1, 1]}).to_parquet(
        calendar
    )
    positions = reconcile_holdings(
        pd.DataFrame(
            {
                "instrument": ["B"],
                "quantity": [1000],
                "available_quantity": [1000],
                "as_of_trade_date": ["2026-01-06"],
                "snapshot_at_utc": [captured],
            }
        ),
        None,
        as_of_date="2026-01-06",
        calendar_path=calendar,
        ledger_path=tmp_path / "ledger.parquet",
        initial_holdings=pd.DataFrame({"instrument": ["B"], "opened_trade_date": ["2026-01-05"]}),
    )
    scores = governed_artifact(
        pd.DataFrame(
            {
                "instrument": ["A", "B"],
                "score": [0.9, 0.8],
                "signal_date": ["2026-01-05"] * 2,
                "trade_date": ["2026-01-06"] * 2,
            }
        ),
        ArtifactType.MODEL_SCORE,
    )
    quotes = _quotes(["A", "B"]).assign(
        as_of_trade_date="2026-01-06",
        snapshot_at_utc=captured,
    )

    decision, orders, blocked = build_topk_orders(
        scores,
        positions,
        quotes,
        signal_date="2026-01-05",
        trade_date="2026-01-06",
        cash=10_000,
        daily_pnl_pct=0.0,
    )

    assert not decision.empty
    assert not orders.empty
    assert blocked.empty


def test_topk_rejects_internal_ledger_quantity_contract(governed_artifact):
    captured = pd.Timestamp.now(tz="UTC").isoformat()
    scores = governed_artifact(
        pd.DataFrame(
            {
                "instrument": ["A"],
                "score": [0.9],
                "signal_date": ["2026-01-05"],
                "trade_date": ["2026-01-06"],
            }
        ),
        ArtifactType.MODEL_SCORE,
    )
    positions = pd.DataFrame(
        {
            "instrument": ["A"],
            "last_quantity": [100],
            "available_quantity": [100],
            "holding_days": [2],
            "as_of_trade_date": ["2026-01-06"],
            "snapshot_at_utc": [captured],
        }
    )
    quotes = _quotes(["A"]).assign(as_of_trade_date="2026-01-06", snapshot_at_utc=captured)

    with pytest.raises(ValueError, match="positions missing columns:.*quantity"):
        build_topk_orders(
            scores,
            positions,
            quotes,
            signal_date="2026-01-05",
            trade_date="2026-01-06",
            cash=10_000,
            daily_pnl_pct=0.0,
        )
