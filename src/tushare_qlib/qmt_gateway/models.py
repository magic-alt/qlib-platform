from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QmtAsset:
    total_asset: float
    cash: float


@dataclass(frozen=True)
class QmtPosition:
    stock_code: str
    volume: float
    can_use_volume: float


@dataclass(frozen=True)
class QmtOrder:
    order_id: str
    stock_code: str
    side: str
    order_volume: float
    traded_volume: float
    price: float
    status: str
    event_at_utc: str
    raw_status: int | str


@dataclass(frozen=True)
class QmtFill:
    trade_id: str
    order_id: str
    stock_code: str
    side: str
    traded_volume: float
    traded_price: float
    event_at_utc: str


@dataclass(frozen=True)
class QmtQuote:
    stock_code: str
    price: float
    paused: int
    is_limit_up: int
    is_limit_down: int
    adv20_volume: float
    adv20_amount: float
