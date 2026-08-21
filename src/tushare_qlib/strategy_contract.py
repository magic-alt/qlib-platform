from __future__ import annotations

from typing import Any

import pandas as pd


def strategy_contract_from_audit_decision(decision: pd.DataFrame) -> dict[str, Any]:
    """Serialize the four distinct strategy states from one audited decision."""

    required = {"signal_date", "trade_date", "instrument", "score", "target_action"}
    missing = required - set(decision.columns)
    if missing:
        raise ValueError(f"audit decision missing strategy-contract columns: {sorted(missing)}")
    frame = decision.copy()
    signal_date = str(frame["signal_date"].dropna().iloc[-1])
    trade_date = str(frame["trade_date"].dropna().iloc[-1])
    candidates = frame.loc[frame.get("is_model_topk", False).astype(bool)].sort_values(
        "score", ascending=False
    )
    candidates_payload = [
        {"instrument": str(row.instrument), "score": float(row.score), "scoreRank": int(row.score_rank)}
        for row in candidates.itertuples(index=False)
    ]
    current = set(
        frame.loc[
            pd.to_numeric(frame.get("quantity_before", 0.0), errors="coerce").fillna(0.0).gt(0), "instrument"
        ]
        .astype(str)
        .tolist()
    )
    sells = set(frame.loc[frame["target_action"].eq("SELL"), "instrument"].astype(str))
    buys = set(frame.loc[frame["target_action"].eq("BUY"), "instrument"].astype(str))
    expected = sorted((current - sells) | buys)
    risk_degree = float(frame.get("risk_degree", pd.Series([1.0])).iloc[0])
    weight = risk_degree / len(expected) if expected else 0.0
    positions = [
        {
            "instrument": instrument,
            "targetWeight": weight,
            "source": "RETAIN" if instrument in current else "BUY",
        }
        for instrument in expected
    ]
    orders = [
        {
            "instrument": str(row.instrument),
            "action": str(row.target_action),
            "score": float(row.score),
            "reason": str(row.action_reason),
        }
        for row in frame.loc[frame["target_action"].isin(["BUY", "SELL"])].itertuples(index=False)
    ]
    dates = {"signalDate": signal_date, "tradeDate": trade_date}
    return {
        "schemaVersion": "1.0",
        "modelTopkCandidates": {
            "artifactType": "MODEL_TOPK_CANDIDATES",
            **dates,
            "candidates": candidates_payload,
        },
        "strategyTargetPositions": {
            "artifactType": "STRATEGY_TARGET_POSITIONS",
            **dates,
            "positions": positions,
        },
        "nextTradeOrders": {"artifactType": "NEXT_TRADE_ORDERS", **dates, "orders": orders},
        "expectedPostTradePositions": {
            "artifactType": "EXPECTED_POST_TRADE_POSITIONS",
            **dates,
            "positions": positions,
        },
    }
