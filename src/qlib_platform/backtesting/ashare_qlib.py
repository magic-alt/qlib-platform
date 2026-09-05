from __future__ import annotations

import math
from collections import defaultdict
from types import MethodType
from typing import Any, cast

import pandas as pd
from qlib.backtest.decision import Order

from qlib_platform.backtesting.ashare_rules import (
    AShareMarketRules,
    is_legal_buy_quantity,
    normalize_buy_quantity,
)

_GUARD_ATTR = "_qlib_platform_ashare_exchange_guard"


def _trade_date(order: object) -> pd.Timestamp:
    return pd.Timestamp(getattr(order, "start_time")).normalize()


def _position_amount(position: object, instrument: str) -> float:
    check_stock = getattr(position, "check_stock", None)
    if callable(check_stock) and not bool(check_stock(instrument)):
        return 0.0
    getter = getattr(position, "get_stock_amount", None)
    if not callable(getter):
        raise RuntimeError("A-share Exchange guard requires a position with get_stock_amount()")
    return float(getter(instrument))


def _order_factor(exchange: object, order: object) -> float:
    factor = getattr(exchange, "get_factor")(
        stock_id=getattr(order, "stock_id"),
        start_time=getattr(order, "start_time"),
        end_time=getattr(order, "end_time"),
    )
    if factor is None or not math.isfinite(float(factor)) or float(factor) <= 0:
        raise RuntimeError(
            "A_SHARE_EXECUTION_VIOLATION: factor unavailable; raw-share legality cannot be verified"
        )
    return float(factor)


def normalize_qlib_buy_amount(
    exchange: object,
    instrument: str,
    amount: float,
    start_time: object,
    end_time: object,
    rules: AShareMarketRules,
) -> float:
    """Normalize a Qlib adjusted amount through the canonical raw-share rules."""

    factor = getattr(exchange, "get_factor")(
        stock_id=instrument,
        start_time=start_time,
        end_time=end_time,
    )
    if factor is None or not math.isfinite(float(factor)) or float(factor) <= 0:
        raise RuntimeError(
            "A_SHARE_EXECUTION_VIOLATION: factor unavailable; raw-share legality cannot be verified"
        )
    factor_value = float(factor)
    raw_quantity = float(amount) * factor_value
    legal_raw = normalize_buy_quantity(instrument, raw_quantity, rules)
    return float(legal_raw / factor_value)


class AShareQlibExchangeGuard:
    """Fail-closed A-share legality layer for Qlib's shared Exchange instance.

    Qlib owns price/volume/cost calculation and account mutation. This guard
    adds cash-equity constraints that the generic Exchange does not model:
    same-session buys remain locked for T+1, sell requests cannot exceed settled
    inventory, and every non-zero buy fill must satisfy the canonical board-size
    rules in :class:`AShareMarketRules`.
    """

    def __init__(self, exchange: object, rules: AShareMarketRules) -> None:
        rules.validate()
        self.exchange = exchange
        self.rules = rules
        self.same_day_buys: defaultdict[tuple[pd.Timestamp, str], float] = defaultdict(float)
        self._original_deal_order = getattr(exchange, "deal_order")
        self._original_calc_trade_info = getattr(exchange, "_calc_trade_info_by_order")

    def _resolved_position(self, trade_account: object | None, position: object | None) -> object | None:
        if trade_account is not None and position is not None:
            raise ValueError("trade_account and position can only choose one")
        if trade_account is not None:
            return cast(object, getattr(trade_account, "current_position"))
        return position

    def _validate_sell(
        self,
        order: object,
        trade_account: object | None,
        position: object | None,
    ) -> None:
        resolved = self._resolved_position(trade_account, position)
        if resolved is None:
            raise RuntimeError("A_SHARE_EXECUTION_VIOLATION: sell requires a known long position")
        instrument = str(getattr(order, "stock_id"))
        total = _position_amount(resolved, instrument)
        locked = self.same_day_buys[(_trade_date(order), instrument)]
        available = max(0.0, total - locked)
        requested = float(getattr(order, "amount"))
        if requested > available + 1e-9:
            raise RuntimeError(
                "A_SHARE_EXECUTION_VIOLATION: T+1 or oversell blocked "
                f"for {instrument}: requested={requested}, settled_available={available}"
            )

    def _validate_requested_buy(self, order: object) -> None:
        factor = _order_factor(self.exchange, order)
        raw_quantity = float(getattr(order, "amount")) * factor
        if not is_legal_buy_quantity(str(getattr(order, "stock_id")), raw_quantity, self.rules):
            raise RuntimeError(
                "A_SHARE_EXECUTION_VIOLATION: illegal A-share buy quantity "
                f"for {getattr(order, 'stock_id')}: raw_quantity={raw_quantity}"
            )

    def calc_trade_info(
        self,
        order: object,
        position: object | None,
        dealt_order_amount: dict[str, float],
    ) -> tuple[float, float, float]:
        trade_price, trade_val, trade_cost = self._original_calc_trade_info(
            order,
            position,
            dealt_order_amount,
        )
        if getattr(order, "direction") == Order.BUY and float(getattr(order, "deal_amount", 0.0)) > 0:
            factor = float(getattr(order, "factor"))
            raw_fill = float(getattr(order, "deal_amount")) * factor
            if not is_legal_buy_quantity(str(getattr(order, "stock_id")), raw_fill, self.rules):
                setattr(order, "deal_amount", 0.0)
                return float(trade_price), 0.0, 0.0
        return float(trade_price), float(trade_val), float(trade_cost)

    def deal_order(
        self,
        order: object,
        trade_account: object | None = None,
        position: object | None = None,
        dealt_order_amount: dict[str, float] | None = None,
    ) -> tuple[float, float, float]:
        check_order = getattr(self.exchange, "check_order")
        if bool(check_order(order)):
            if getattr(order, "direction") == Order.SELL:
                self._validate_sell(order, trade_account, position)
            elif getattr(order, "direction") == Order.BUY and float(getattr(order, "amount")) > 0:
                self._validate_requested_buy(order)

        kwargs: dict[str, Any] = {"trade_account": trade_account, "position": position}
        if dealt_order_amount is not None:
            kwargs["dealt_order_amount"] = dealt_order_amount
        trade_val, trade_cost, trade_price = self._original_deal_order(order, **kwargs)

        if (
            trade_account is not None
            and getattr(order, "direction") == Order.BUY
            and float(getattr(order, "deal_amount", 0.0)) > 0
        ):
            key = (_trade_date(order), str(getattr(order, "stock_id")))
            self.same_day_buys[key] += float(getattr(order, "deal_amount"))
        return float(trade_val), float(trade_cost), float(trade_price)


def guard_qlib_exchange(
    exchange: object,
    rules: AShareMarketRules | None = None,
) -> AShareQlibExchangeGuard:
    """Attach one A-share guard to the exact Qlib Exchange shared by the executor."""

    existing = getattr(exchange, _GUARD_ATTR, None)
    if isinstance(existing, AShareQlibExchangeGuard):
        return existing

    guard = AShareQlibExchangeGuard(exchange, rules or AShareMarketRules())

    def guarded_deal_order(
        _exchange: object,
        order: object,
        trade_account: object | None = None,
        position: object | None = None,
        dealt_order_amount: dict[str, float] | None = None,
    ) -> tuple[float, float, float]:
        return guard.deal_order(order, trade_account, position, dealt_order_amount)

    def guarded_calc_trade_info(
        _exchange: object,
        order: object,
        position: object | None,
        dealt_order_amount: dict[str, float],
    ) -> tuple[float, float, float]:
        return guard.calc_trade_info(order, position, dealt_order_amount)

    setattr(exchange, "deal_order", MethodType(guarded_deal_order, exchange))
    setattr(exchange, "_calc_trade_info_by_order", MethodType(guarded_calc_trade_info, exchange))
    setattr(exchange, _GUARD_ATTR, guard)
    return guard
