from __future__ import annotations

from typing import Any

import pandas as pd

from qlib_platform.backtesting.topk_dropout import TopkDropoutPolicy, topk_dropout_decision


def latest_strategy_targets(
    scores: pd.Series,
    positions: pd.DataFrame,
    quotes: pd.DataFrame,
    *,
    policy: TopkDropoutPolicy,
    signal_date: pd.Timestamp,
    trade_date: pd.Timestamp,
) -> dict[str, Any]:
    """Separate model candidates from stateful strategy intents and orders.

    A TopkDropout model TopK is *not* a tradable target portfolio.  The latter
    depends on inventory, minimum holding periods and tradeability on the next
    execution date.  This contract exposes every stage so an execution adapter
    cannot accidentally treat raw model rankings as target holdings.
    """

    ranked = scores.sort_values(ascending=False)
    candidates = [
        {"instrument": str(instrument), "score": float(score), "scoreRank": index + 1}
        for index, (instrument, score) in enumerate(ranked.head(policy.topk).items())
    ]
    decision = topk_dropout_decision(
        scores,
        positions,
        quotes,
        policy=policy,
        signal_date=signal_date,
        trade_date=trade_date,
    )
    current = set(positions["instrument"].astype(str)) if not positions.empty else set()
    sells = set(decision.loc[decision["target_action"] == "SELL", "instrument"].astype(str))
    buys = decision.loc[decision["target_action"] == "BUY", ["instrument", "score", "score_rank"]]
    expected = (current - sells) | set(buys["instrument"].astype(str))
    target_weight = policy.risk_degree / len(expected) if expected else 0.0
    expected_positions = [
        {
            "instrument": instrument,
            "targetWeight": target_weight,
            "source": "RETAIN" if instrument in current else "BUY",
        }
        for instrument in sorted(expected)
    ]
    orders = [
        {
            "instrument": str(row.instrument),
            "action": str(row.target_action),
            "score": float(row.score),
            "reason": str(row.action_reason),
        }
        for row in decision.loc[decision["target_action"].isin(["BUY", "SELL"])].itertuples(index=False)
    ]
    target_positions = [
        {
            "instrument": item["instrument"],
            "targetWeight": item["targetWeight"],
            "state": item["source"],
        }
        for item in expected_positions
    ]
    dates = {"signalDate": signal_date.strftime("%Y-%m-%d"), "tradeDate": trade_date.strftime("%Y-%m-%d")}
    return {
        "schemaVersion": "1.0",
        "modelTopkCandidates": {"artifactType": "MODEL_TOPK_CANDIDATES", **dates, "candidates": candidates},
        "strategyTargetPositions": {
            "artifactType": "STRATEGY_TARGET_POSITIONS",
            **dates,
            "positions": target_positions,
        },
        "nextTradeOrders": {"artifactType": "NEXT_TRADE_ORDERS", **dates, "orders": orders},
        "expectedPostTradePositions": {
            "artifactType": "EXPECTED_POST_TRADE_POSITIONS",
            **dates,
            "positions": expected_positions,
            "assumption": "all planned orders fill at the configured execution price",
        },
    }
