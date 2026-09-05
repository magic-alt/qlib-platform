from __future__ import annotations

from collections import defaultdict

import pandas as pd
import pytest
from qlib.backtest.decision import Order

from qlib_platform.backtesting.ashare_qlib import guard_qlib_exchange
from qlib_platform.backtesting.ashare_rules import AShareMarketRules


class _FakePosition:
    def __init__(self, **amounts: float) -> None:
        self.amounts = dict(amounts)

    def check_stock(self, instrument: str) -> bool:
        return self.amounts.get(instrument, 0.0) > 0

    def get_stock_amount(self, instrument: str) -> float:
        return float(self.amounts.get(instrument, 0.0))

    def apply(self, order: Order) -> None:
        current = self.get_stock_amount(order.stock_id)
        if order.direction == Order.BUY:
            self.amounts[order.stock_id] = current + float(order.deal_amount)
        else:
            self.amounts[order.stock_id] = current - float(order.deal_amount)


class _FakeAccount:
    def __init__(self, position: _FakePosition) -> None:
        self.current_position = position


class _FakeExchange:
    def __init__(self, *, clip_buy_to: float | None = None) -> None:
        self.calls = 0
        self.clip_buy_to = clip_buy_to

    def check_order(self, order: Order) -> bool:
        return True

    def get_factor(self, **kwargs: object) -> float:
        return 1.0

    def _calc_trade_info_by_order(
        self,
        order: Order,
        position: _FakePosition | None,
        dealt_order_amount: dict[str, float],
    ) -> tuple[float, float, float]:
        order.factor = 1.0
        amount = float(order.amount)
        if order.direction == Order.BUY and self.clip_buy_to is not None:
            amount = min(amount, self.clip_buy_to)
        order.deal_amount = amount
        return 10.0, amount * 10.0, amount * 0.001

    def deal_order(
        self,
        order: Order,
        trade_account: _FakeAccount | None = None,
        position: _FakePosition | None = None,
        dealt_order_amount: dict[str, float] | None = None,
    ) -> tuple[float, float, float]:
        self.calls += 1
        resolved = trade_account.current_position if trade_account is not None else position
        trade_price, trade_val, trade_cost = self._calc_trade_info_by_order(
            order,
            resolved,
            dealt_order_amount or defaultdict(float),
        )
        if resolved is not None and trade_val > 0:
            resolved.apply(order)
        return trade_val, trade_cost, trade_price


def _order(instrument: str, amount: float, direction: int, date: str = "2026-09-01") -> Order:
    stamp = pd.Timestamp(date)
    return Order(
        stock_id=instrument,
        amount=amount,
        start_time=stamp,
        end_time=stamp,
        direction=direction,
    )


def test_exchange_guard_blocks_same_day_new_inventory_but_allows_settled_inventory() -> None:
    exchange = _FakeExchange()
    guard_qlib_exchange(exchange, AShareMarketRules())
    position = _FakePosition(SH600000=100.0)
    account = _FakeAccount(position)

    exchange.deal_order(_order("SH600000", 100, Order.BUY), trade_account=account)
    exchange.deal_order(_order("SH600000", 100, Order.SELL), trade_account=account)

    assert position.get_stock_amount("SH600000") == 100.0
    with pytest.raises(RuntimeError, match="T\+1 or oversell blocked"):
        exchange.deal_order(_order("SH600000", 100, Order.SELL), trade_account=account)


def test_exchange_guard_releases_locked_inventory_on_next_trade_date() -> None:
    exchange = _FakeExchange()
    guard_qlib_exchange(exchange, AShareMarketRules())
    position = _FakePosition()
    account = _FakeAccount(position)

    exchange.deal_order(_order("SH600000", 100, Order.BUY, "2026-09-01"), trade_account=account)
    exchange.deal_order(_order("SH600000", 100, Order.SELL, "2026-09-02"), trade_account=account)

    assert position.get_stock_amount("SH600000") == 0.0


def test_exchange_guard_rejects_naked_short_before_underlying_fill() -> None:
    exchange = _FakeExchange()
    guard_qlib_exchange(exchange, AShareMarketRules())
    account = _FakeAccount(_FakePosition())

    with pytest.raises(RuntimeError, match="T\+1 or oversell blocked"):
        exchange.deal_order(_order("SH600000", 100, Order.SELL), trade_account=account)

    assert exchange.calls == 0


@pytest.mark.parametrize(
    ("instrument", "amount"),
    [("SH600000", 150), ("SH688981", 199)],
)
def test_exchange_guard_rejects_illegal_requested_buy_quantities(
    instrument: str,
    amount: float,
) -> None:
    exchange = _FakeExchange()
    guard_qlib_exchange(exchange, AShareMarketRules())
    account = _FakeAccount(_FakePosition())

    with pytest.raises(RuntimeError, match="illegal A-share buy quantity"):
        exchange.deal_order(_order(instrument, amount, Order.BUY), trade_account=account)

    assert exchange.calls == 0


def test_exchange_guard_accepts_star_one_share_increment_above_200() -> None:
    exchange = _FakeExchange()
    guard_qlib_exchange(exchange, AShareMarketRules())
    position = _FakePosition()
    account = _FakeAccount(position)

    exchange.deal_order(_order("SH688981", 399, Order.BUY), trade_account=account)

    assert position.get_stock_amount("SH688981") == 399.0


def test_exchange_guard_rejects_illegal_post_clip_star_fill_before_account_update() -> None:
    exchange = _FakeExchange(clip_buy_to=150)
    guard_qlib_exchange(exchange, AShareMarketRules())
    position = _FakePosition()
    account = _FakeAccount(position)

    trade_val, trade_cost, _ = exchange.deal_order(
        _order("SH688981", 399, Order.BUY),
        trade_account=account,
    )

    assert trade_val == 0.0
    assert trade_cost == 0.0
    assert position.get_stock_amount("SH688981") == 0.0


def test_exchange_guard_is_idempotent() -> None:
    exchange = _FakeExchange()

    first = guard_qlib_exchange(exchange, AShareMarketRules())
    wrapped_deal_order = exchange.deal_order
    second = guard_qlib_exchange(exchange, AShareMarketRules())

    assert second is first
    assert exchange.deal_order is wrapped_deal_order
