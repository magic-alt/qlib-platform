import pandas as pd

from tushare_qlib.execution import ExecutionPolicy, build_orders


def test_t1_and_price_limit_blocks_orders():
    targets = pd.DataFrame(
        {"instrument": ["SH600000", "SZ000001"], "target_weight": [0.0, 0.5], "score": [0.1, 0.9]}
    )
    positions = pd.DataFrame(
        {
            "instrument": ["SH600000", "SZ000001"],
            "quantity": [1000, 0],
            "available_quantity": [0, 0],
        }
    )
    quotes = pd.DataFrame(
        {
            "instrument": ["SH600000", "SZ000001"],
            "price": [10.0, 10.0],
            "paused": [0, 0],
            "is_limit_up": [0, 1],
            "is_limit_down": [0, 0],
            "adv20_volume": [100000, 100000],
        }
    )
    orders, blocked = build_orders(
        targets,
        positions,
        quotes,
        trade_date="2026-08-07",
        portfolio_value=100000,
        cash=100000,
        policy=ExecutionPolicy(),
    )
    assert orders.empty
    assert set(blocked["reason"]) == {"T1_NOT_SELLABLE", "LIMIT_UP"}


def test_orders_are_lot_sized_and_idempotent():
    targets = pd.DataFrame({"instrument": ["SH600000"], "target_weight": [0.5], "score": [1.0]})
    positions = pd.DataFrame({"instrument": ["SH600000"], "quantity": [0], "available_quantity": [0]})
    quotes = pd.DataFrame(
        {"instrument": ["SH600000"], "price": [10.0], "paused": [0], "is_limit_up": [0], "is_limit_down": [0]}
    )
    first, _ = build_orders(targets, positions, quotes, trade_date="2026-08-07", portfolio_value=100000, cash=100000)
    second, _ = build_orders(targets, positions, quotes, trade_date="2026-08-07", portfolio_value=100000, cash=100000)
    assert first.iloc[0]["quantity"] % 100 == 0
    assert first.iloc[0]["client_order_id"] == second.iloc[0]["client_order_id"]
