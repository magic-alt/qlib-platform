from __future__ import annotations

import pandas as pd

from qlib_platform.backtesting.strategy_contract import strategy_contract_from_audit_decision


def test_strategy_contract_distinguishes_candidates_targets_orders_and_expected_inventory() -> None:
    decision = pd.DataFrame(
        {
            "signal_date": ["2026-01-05", "2026-01-05", "2026-01-05"],
            "trade_date": ["2026-01-06", "2026-01-06", "2026-01-06"],
            "instrument": ["SH600000", "SZ000001", "SZ000002"],
            "score": [0.9, 0.8, 0.1],
            "score_rank": [1, 2, 3],
            "is_model_topk": [True, True, False],
            "target_action": ["BUY", "HOLD", "SELL"],
            "action_reason": ["TOPK_FILL_OR_REPLACEMENT", "CURRENT_POSITION", "DROP_LOWEST_COMBINED_SCORE"],
            "quantity_before": [0.0, 100.0, 100.0],
            "risk_degree": [0.95, 0.95, 0.95],
        }
    )

    contract = strategy_contract_from_audit_decision(decision)

    assert len(contract["modelTopkCandidates"]["candidates"]) == 2
    assert contract["nextTradeOrders"]["orders"] == [
        {"instrument": "SH600000", "action": "BUY", "score": 0.9, "reason": "TOPK_FILL_OR_REPLACEMENT"},
        {"instrument": "SZ000002", "action": "SELL", "score": 0.1, "reason": "DROP_LOWEST_COMBINED_SCORE"},
    ]
    assert {item["instrument"] for item in contract["expectedPostTradePositions"]["positions"]} == {
        "SH600000",
        "SZ000001",
    }
