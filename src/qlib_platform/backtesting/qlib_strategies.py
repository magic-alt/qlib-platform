from __future__ import annotations

import copy
from typing import Any

import numpy as np
import pandas as pd

from qlib.backtest.decision import Order, OrderDir, TradeDecisionWO
from qlib.backtest.position import Position
from qlib.contrib.strategy.signal_strategy import BaseSignalStrategy

from qlib_platform.backtesting.topk_dropout import RankBufferPolicy


class RankBufferStrategy(BaseSignalStrategy):
    """Qlib execution strategy implementing the pre-registered rank buffer.

    The decision mirrors :func:`qlib_platform.backtesting.topk_dropout.rank_buffer_decision`
    so that the formal backtest, the decision replay and the StrategyAudit
    agree on the same buy/sell intent:

    * Holdings whose rank has breached ``exit_rank`` are sell candidates,
      worst rank first, capped by ``max_replacements`` per day.
    * Sells are skipped while ``hold_thresh`` or tradability blocks them;
      the corresponding slot then stays open for refill.
    * Open slots up to ``target_size`` are refilled from names ranked within
      ``entry_rank``, again capped by ``max_replacements`` per day.

    Order sizing, board-lot rounding, risk degree and cash management reuse
    Qlib's ``TopkDropoutStrategy`` execution semantics (sell first, then buy
    with ``cash * risk_degree / n_buys``).
    """

    def __init__(
        self,
        *,
        target_size: int,
        entry_rank: int,
        exit_rank: int,
        max_replacements: int,
        hold_thresh: int = 1,
        only_tradable: bool = True,
        forbid_all_trade_at_limit: bool = True,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.target_size = target_size
        self.entry_rank = entry_rank
        self.exit_rank = exit_rank
        self.max_replacements = max_replacements
        self.hold_thresh = hold_thresh
        self.only_tradable = only_tradable
        self.forbid_all_trade_at_limit = forbid_all_trade_at_limit
        RankBufferPolicy(
            target_size=target_size,
            entry_rank=entry_rank,
            exit_rank=exit_rank,
            max_replacements=max_replacements,
            hold_thresh=hold_thresh,
            only_tradable=only_tradable,
            forbid_all_trade_at_limit=forbid_all_trade_at_limit,
            risk_degree=self.risk_degree,
        ).validate()

    def generate_trade_decision(self, execute_result=None):  # type: ignore[no-untyped-def]
        trade_step = self.trade_calendar.get_trade_step()
        trade_start_time, trade_end_time = self.trade_calendar.get_step_time(trade_step)
        pred_start_time, pred_end_time = self.trade_calendar.get_step_time(trade_step, shift=1)
        pred_score = self.signal.get_signal(start_time=pred_start_time, end_time=pred_end_time)
        if isinstance(pred_score, pd.DataFrame):
            pred_score = pred_score.iloc[:, 0]
        if pred_score is None:
            return TradeDecisionWO([], self)

        def is_tradable(stock_id: str, direction: int | None) -> bool:
            return bool(
                self.trade_exchange.is_stock_tradable(
                    stock_id=stock_id,
                    start_time=trade_start_time,
                    end_time=trade_end_time,
                    direction=direction,
                )
            )

        sell_direction: int | None = None if self.forbid_all_trade_at_limit else OrderDir.SELL
        buy_direction: int | None = None if self.forbid_all_trade_at_limit else OrderDir.BUY

        current_temp: Position = copy.deepcopy(self.trade_position)
        cash = current_temp.get_cash()
        current_stock_list = current_temp.get_stock_list()
        ranked = pred_score.sort_values(ascending=False)
        ranks = pd.Series(np.arange(1, len(ranked) + 1), index=ranked.index)

        sell_candidates = sorted(
            (
                str(instrument)
                for instrument in current_stock_list
                if not np.isfinite(float(ranks.get(instrument, np.nan)))
                or int(ranks[instrument]) > self.exit_rank
            ),
            key=lambda instrument: (-int(ranks.get(instrument, len(ranks) + 1)), instrument),
        )
        sells: list[str] = []
        for instrument in sell_candidates:
            if len(sells) >= self.max_replacements:
                break
            if (
                current_temp.get_stock_count(instrument, bar=self.trade_calendar.get_freq())
                < self.hold_thresh
            ):
                continue
            if self.only_tradable and not is_tradable(instrument, sell_direction):
                continue
            sells.append(instrument)
        retained = [str(value) for value in current_stock_list if str(value) not in set(sells)]
        slots = max(0, self.target_size - len(retained))
        buys: list[str] = []
        for instrument in ranked.index:
            name = str(instrument)
            if len(buys) >= min(slots, self.max_replacements) or int(ranks[name]) > self.entry_rank:
                break
            if name in retained or name in set(sells):
                continue
            if self.only_tradable and not is_tradable(name, buy_direction):
                continue
            buys.append(name)

        sell_order_list = []
        for code in sells:
            sell_amount = current_temp.get_stock_amount(code=code)
            sell_order = Order(
                stock_id=code,
                amount=sell_amount,
                start_time=trade_start_time,
                end_time=trade_end_time,
                direction=Order.SELL,
            )
            if self.trade_exchange.check_order(sell_order):
                sell_order_list.append(sell_order)
                trade_val, trade_cost, _ = self.trade_exchange.deal_order(sell_order, position=current_temp)
                cash += trade_val - trade_cost

        buy_order_list = []
        if len(buys) > 0:
            value = cash * self.risk_degree / len(buys)
            for code in buys:
                buy_price = self.trade_exchange.get_deal_price(
                    stock_id=code,
                    start_time=trade_start_time,
                    end_time=trade_end_time,
                    direction=OrderDir.BUY,
                )
                buy_amount = value / buy_price
                factor = self.trade_exchange.get_factor(
                    stock_id=code, start_time=trade_start_time, end_time=trade_end_time
                )
                buy_amount = self.trade_exchange.round_amount_by_trade_unit(buy_amount, factor)
                buy_order_list.append(
                    Order(
                        stock_id=code,
                        amount=buy_amount,
                        start_time=trade_start_time,
                        end_time=trade_end_time,
                        direction=Order.BUY,
                    )
                )
        return TradeDecisionWO(sell_order_list + buy_order_list, self)
