import pandas as pd

from tushare_qlib.artifacts import ArtifactType
from tushare_qlib.execution import ExecutionPolicy, build_orders


def _fresh(frame: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    return frame.assign(as_of_trade_date=trade_date, snapshot_at_utc=pd.Timestamp.now(tz="UTC").isoformat())


def test_t1_and_price_limit_blocks_orders(governed_artifact):
    targets = governed_artifact(
        pd.DataFrame(
            {"instrument": ["SH600000", "SZ000001"], "target_weight": [0.0, 0.5], "score": [0.1, 0.9]}
        ),
        ArtifactType.TARGET_PORTFOLIO,
    )
    positions = _fresh(
        pd.DataFrame(
            {
                "instrument": ["SH600000", "SZ000001"],
                "quantity": [1000, 0],
                "available_quantity": [0, 0],
            }
        ),
        "2026-08-07",
    )
    quotes = _fresh(
        pd.DataFrame(
            {
                "instrument": ["SH600000", "SZ000001"],
                "price": [10.0, 10.0],
                "paused": [0, 0],
                "is_limit_up": [0, 1],
                "is_limit_down": [0, 0],
                "adv20_volume": [100000, 100000],
            }
        ),
        "2026-08-07",
    )
    orders, blocked = build_orders(
        targets,
        positions,
        quotes,
        trade_date="2026-08-07",
        portfolio_value=100000,
        cash=100000,
        policy=ExecutionPolicy(),
        daily_pnl_pct=0.0,
    )
    assert orders.empty
    assert set(blocked["reason"]) == {"T1_NOT_SELLABLE", "LIMIT_UP"}


def test_orders_are_lot_sized_and_idempotent(governed_artifact):
    targets = governed_artifact(
        pd.DataFrame({"instrument": ["SH600000"], "target_weight": [0.5], "score": [1.0]}),
        ArtifactType.TARGET_PORTFOLIO,
    )
    positions = _fresh(
        pd.DataFrame({"instrument": ["SH600000"], "quantity": [0], "available_quantity": [0]}), "2026-08-07"
    )
    quotes = _fresh(
        pd.DataFrame(
            {
                "instrument": ["SH600000"],
                "price": [10.0],
                "paused": [0],
                "is_limit_up": [0],
                "is_limit_down": [0],
            }
        ),
        "2026-08-07",
    )
    first, _ = build_orders(
        targets,
        positions,
        quotes,
        trade_date="2026-08-07",
        portfolio_value=100000,
        cash=100000,
        daily_pnl_pct=0.0,
    )
    second, _ = build_orders(
        targets,
        positions,
        quotes,
        trade_date="2026-08-07",
        portfolio_value=100000,
        cash=100000,
        daily_pnl_pct=0.0,
    )
    assert first.iloc[0]["quantity"] % 100 == 0
    assert first.iloc[0]["client_order_id"] == second.iloc[0]["client_order_id"]
    assert first.iloc[0]["artifact_type"] == ArtifactType.ORDER_INTENT.value
