from __future__ import annotations

import pandas as pd

from tushare_qlib.strategy_targets import latest_strategy_targets
from tushare_qlib.topk_dropout import TopkDropoutPolicy


def test_latest_strategy_targets_keeps_model_candidates_separate_from_orders_and_post_trade_inventory() -> (
    None
):
    result = latest_strategy_targets(
        pd.Series({"SH600000": 0.9, "SZ000001": 0.8, "SZ000002": 0.1}),
        pd.DataFrame({"instrument": ["SZ000002"], "quantity": [100.0], "holding_days": [5]}),
        pd.DataFrame(
            {
                "instrument": ["SH600000", "SZ000001", "SZ000002"],
                "paused": [0, 0, 0],
                "is_limit_up": [0, 0, 0],
                "is_limit_down": [0, 0, 0],
            }
        ),
        policy=TopkDropoutPolicy(topk=2, n_drop=1, hold_thresh=1),
        signal_date=pd.Timestamp("2026-01-05"),
        trade_date=pd.Timestamp("2026-01-06"),
    )

    assert set(result) == {
        "schemaVersion",
        "modelTopkCandidates",
        "strategyTargetPositions",
        "nextTradeOrders",
        "expectedPostTradePositions",
    }
    assert result["modelTopkCandidates"]["artifactType"] == "MODEL_TOPK_CANDIDATES"
    assert result["nextTradeOrders"]["artifactType"] == "NEXT_TRADE_ORDERS"
