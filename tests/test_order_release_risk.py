from __future__ import annotations


import pandas as pd
import pytest

from tushare_qlib.artifacts import ArtifactType
from tushare_qlib.execution import build_topk_orders
from tushare_qlib.risk_engine import RiskLimitError


BASE_RISK = {
    "max_gross_exposure": 0.95,
    "max_single_name": 0.50,
    "max_sector_exposure": 0.60,
    "max_daily_loss": 0.03,
    "kill_switch": False,
}


def test_topk_order_builder_cannot_bypass_manifest_kill_switch(governed_artifact):
    scores = governed_artifact(
        pd.DataFrame(
            {
                "instrument": ["SH600000"],
                "score": [1.0],
                "sector": ["bank"],
                "signal_date": ["2026-08-10"],
                "trade_date": ["2026-08-11"],
            }
        ),
        ArtifactType.MODEL_SCORE,
        risk={**BASE_RISK, "kill_switch": True},
    )
    snapshot = pd.Timestamp.now(tz="UTC").isoformat()
    positions = pd.DataFrame(
        {
            "instrument": ["SH600000"],
            "quantity": [0],
            "available_quantity": [0],
            "holding_days": [0],
            "as_of_trade_date": ["2026-08-11"],
            "snapshot_at_utc": [snapshot],
        }
    )
    quotes = pd.DataFrame(
        {
            "instrument": ["SH600000"],
            "price": [10.0],
            "paused": [0],
            "is_limit_up": [0],
            "is_limit_down": [0],
            "sector": ["bank"],
            "as_of_trade_date": ["2026-08-11"],
            "snapshot_at_utc": [snapshot],
        }
    )

    with pytest.raises(RiskLimitError, match="kill switch"):
        build_topk_orders(
            scores,
            positions,
            quotes,
            signal_date="2026-08-10",
            trade_date="2026-08-11",
            cash=100_000,
            daily_pnl_pct=0.0,
        )
