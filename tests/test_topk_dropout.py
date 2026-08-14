from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pandas as pd
from tushare_qlib.topk_dropout import (
    TopkDropoutPolicy,
    enforce_deterministic_qlib_position_order,
    topk_dropout_decision,
)


def test_qlib_position_iteration_is_sorted_for_cross_process_determinism():
    from qlib.backtest.position import Position

    enforce_deterministic_qlib_position_order()
    position = Position(
        cash=100.0,
        position_dict={
            "SZ000001": {"amount": 1.0, "price": 1.0},
            "SH600000": {"amount": 1.0, "price": 1.0},
        },
    )

    assert position.get_stock_list() == ["SH600000", "SZ000001"]


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


def test_live_buy_sell_sets_match_official_qlib_topk_dropout(monkeypatch):
    # Qlib's strategy package imports the unrelated convex optimizer eagerly.
    # Stub only that optimizer so this parity test exercises the official
    # TopkDropoutStrategy implementation without requiring cvxpy.
    optimizer = types.ModuleType("qlib.contrib.strategy.optimizer")
    optimizer.EnhancedIndexingOptimizer = object
    monkeypatch.setitem(sys.modules, "qlib.contrib.strategy.optimizer", optimizer)
    from qlib.backtest.decision import OrderDir
    from qlib.contrib.strategy.signal_strategy import TopkDropoutStrategy

    scores = pd.Series({"A": 0.9, "B": 0.8, "C": 0.7, "D": 0.6})

    class Calendar:
        def get_trade_step(self):
            return 0

        def get_step_time(self, step=None, shift=0):
            date = pd.Timestamp("2026-01-05") if shift else pd.Timestamp("2026-01-06")
            return date, date

        def get_freq(self):
            return "day"

    class Signal:
        def get_signal(self, start_time, end_time):
            return scores

    class Position:
        def get_cash(self):
            return 100_000.0

        def get_stock_list(self):
            return ["B", "D"]

        def get_stock_count(self, code, bar):
            return 5

        def get_stock_amount(self, code):
            return 100.0

    class Exchange:
        def is_stock_tradable(self, stock_id, start_time, end_time, direction=None):
            return True

        def check_order(self, order):
            return True

        def deal_order(self, order, position):
            return order.amount * 10.0, 0.0, 10.0

        def get_deal_price(self, stock_id, start_time, end_time, direction):
            return 10.0

        def get_factor(self, stock_id, start_time, end_time):
            return 1.0

        def round_amount_by_trade_unit(self, amount, factor):
            return int(amount // 100) * 100

    qlib_strategy = object.__new__(TopkDropoutStrategy)
    qlib_strategy.level_infra = {"trade_calendar": Calendar()}
    qlib_strategy.common_infra = {"trade_account": SimpleNamespace(current_position=Position())}
    qlib_strategy._trade_exchange = Exchange()
    qlib_strategy.signal = Signal()
    qlib_strategy.topk = 2
    qlib_strategy.n_drop = 1
    qlib_strategy.method_sell = "bottom"
    qlib_strategy.method_buy = "top"
    qlib_strategy.hold_thresh = 5
    qlib_strategy.only_tradable = True
    qlib_strategy.forbid_all_trade_at_limit = True
    qlib_strategy.risk_degree = 0.95
    official_orders = qlib_strategy.generate_trade_decision().get_decision()
    official_buy = {order.stock_id for order in official_orders if order.direction == OrderDir.BUY}
    official_sell = {order.stock_id for order in official_orders if order.direction == OrderDir.SELL}

    live = topk_dropout_decision(
        scores,
        pd.DataFrame(
            {
                "instrument": ["B", "D"],
                "quantity": [100, 100],
                "available_quantity": [100, 100],
                "holding_days": [5, 5],
            }
        ),
        _quotes(list(scores.index)),
        policy=TopkDropoutPolicy(topk=2, n_drop=1, hold_thresh=5),
    )
    live_buy = set(live.loc[live["target_action"] == "BUY", "instrument"])
    live_sell = set(live.loc[live["target_action"] == "SELL", "instrument"])

    assert live_buy == official_buy == {"A"}
    assert live_sell == official_sell == {"D"}
