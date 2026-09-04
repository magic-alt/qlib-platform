from __future__ import annotations

import pandas as pd
import pytest

from qlib_platform.qlib_strategies import RankBufferStrategy
from qlib_platform.topk_dropout import RankBufferPolicy, rank_buffer_decision


def test_rank_buffer_strategy_imports_without_cvxpy_dependency(monkeypatch) -> None:
    # The Qlib contrib strategy package imports the convex optimizer eagerly.
    # RankBufferStrategy must not require it, so stubbing the module must still
    # let us construct and drive the strategy.
    import types
    import sys

    optimizer = types.ModuleType("qlib.contrib.strategy.optimizer")
    optimizer.EnhancedIndexingOptimizer = object
    monkeypatch.setitem(sys.modules, "qlib.contrib.strategy.optimizer", optimizer)

    scores = pd.Series({f"S{rank:02d}": 100 - rank for rank in range(1, 26)})

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
            return ["S03", "S22"]

        def get_stock_count(self, code, bar):
            return 3

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

    qlib_strategy = object.__new__(RankBufferStrategy)
    qlib_strategy.level_infra = {"trade_calendar": Calendar()}
    qlib_strategy.common_infra = {"trade_account": type("A", (), {"current_position": Position()})()}
    qlib_strategy._trade_exchange = Exchange()
    qlib_strategy.signal = Signal()
    qlib_strategy.target_size = 10
    qlib_strategy.entry_rank = 10
    qlib_strategy.exit_rank = 20
    qlib_strategy.max_replacements = 3
    qlib_strategy.hold_thresh = 1
    qlib_strategy.only_tradable = True
    qlib_strategy.forbid_all_trade_at_limit = True
    qlib_strategy.risk_degree = 0.95

    from qlib.backtest.decision import OrderDir

    orders = qlib_strategy.generate_trade_decision().get_decision()
    official_buy = {order.stock_id for order in orders if order.direction == OrderDir.BUY}
    official_sell = {order.stock_id for order in orders if order.direction == OrderDir.SELL}

    assert official_sell == {"S22"}
    # S03 is retained inside the buffer; S22 breaches exit_rank.  One slot opens
    # (target_size=10 vs 1 retained) and max_replacements=3 caps the refill, so
    # S01/S02/S04 enter while the limited S03 stays put.
    assert official_buy == {"S01", "S02", "S04"}


def test_rank_buffer_strategy_matches_rank_buffer_decision(monkeypatch) -> None:
    import types
    import sys

    optimizer = types.ModuleType("qlib.contrib.strategy.optimizer")
    optimizer.EnhancedIndexingOptimizer = object
    monkeypatch.setitem(sys.modules, "qlib.contrib.strategy.optimizer", optimizer)

    scores = pd.Series({f"S{rank:02d}": 100 - rank for rank in range(1, 26)})

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
            return ["S03", "S22"]

        def get_stock_count(self, code, bar):
            return 3

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

    policy = RankBufferPolicy(target_size=10, entry_rank=10, exit_rank=20, max_replacements=3, hold_thresh=1)
    decision = rank_buffer_decision(
        scores,
        pd.DataFrame({"instrument": ["S03", "S22"], "quantity": [100, 100], "holding_days": [3, 3]}),
        pd.DataFrame(
            {
                "instrument": list(scores.index),
                "paused": [0] * len(scores),
                "is_limit_up": [0] * len(scores),
                "is_limit_down": [0] * len(scores),
            }
        ),
        policy=policy,
    )
    planned_buy = set(decision.loc[decision["target_action"] == "BUY", "instrument"])
    planned_sell = set(decision.loc[decision["target_action"] == "SELL", "instrument"])

    assert planned_sell == {"S22"}
    assert planned_buy == {"S01", "S02", "S04"}


def test_rank_buffer_strategy_requires_valid_policy() -> None:
    with pytest.raises(ValueError, match="0 < entry_rank < exit_rank"):
        RankBufferStrategy(
            target_size=10,
            entry_rank=20,
            exit_rank=10,
            max_replacements=3,
            signal=pd.Series(dtype=float),
        )
