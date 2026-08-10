from __future__ import annotations

import pandas as pd

from tushare_qlib.artifacts import ArtifactType
from tushare_qlib.execution import ExecutionPolicy, build_topk_orders
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
        scores.rename("score").rename_axis("instrument").reset_index().assign(
            signal_date="2026-01-05", trade_date="2026-01-06"
        ), ArtifactType.MODEL_SCORE
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
    )
    assert decision.set_index("instrument").at["D", "target_action"] == "SELL"
    assert orders["side"].tolist() == ["BUY"]
    assert orders.iloc[0]["instrument"] == "A"
    assert blocked.iloc[0]["reason"] == "T1_NOT_SELLABLE"
