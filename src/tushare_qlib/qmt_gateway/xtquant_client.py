from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, Protocol, Sequence, TypeVar
from zoneinfo import ZoneInfo

from .config import GatewaySettings
from .models import QmtAsset, QmtFill, QmtOrder, QmtPosition


LOGGER = logging.getLogger(__name__)
T = TypeVar("T")


class QmtReadOnlyClient(Protocol):
    def ensure_connected(self) -> None: ...

    def query_asset(self) -> QmtAsset: ...

    def query_positions(self) -> Sequence[QmtPosition]: ...

    def query_orders(self) -> Sequence[QmtOrder]: ...

    def query_fills(self) -> Sequence[QmtFill]: ...

    def close(self) -> None: ...


_ORDER_STATUS = {
    48: "SUBMITTED",
    49: "SUBMITTED",
    50: "ACKNOWLEDGED",
    51: "CANCEL_REQUESTED",
    52: "CANCEL_REQUESTED",
    53: "CANCELLED",
    54: "CANCELLED",
    55: "PARTIALLY_FILLED",
    56: "FILLED",
    57: "REJECTED",
}


def qmt_to_qlib_symbol(stock_code: str) -> str:
    try:
        code, market = stock_code.strip().upper().split(".")
    except ValueError as exc:
        raise ValueError(f"unsupported QMT stock code: {stock_code!r}") from exc
    if market not in {"SH", "SZ"} or not code.isdigit():
        raise ValueError(f"unsupported QMT stock code: {stock_code!r}")
    return f"{market}{code}"


def _value(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, dict):
        return record.get(name, default)
    return getattr(record, name, default)


def _event_time(value: Any) -> str:
    """Convert QMT epoch seconds (or older compact timestamps) to UTC text."""
    text = str(value or "").strip()
    if len(text) == 14 and text.isdigit():
        local = datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        return local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        timestamp = float(text)
    except ValueError as exc:
        raise ValueError(f"unsupported QMT event time: {value!r}") from exc
    if timestamp <= 0:
        raise ValueError(f"unsupported QMT event time: {value!r}")
    if timestamp > 50_000_000_000:
        timestamp /= 1000
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


class XtQuantReadOnlyClient:
    """Thread-safe, reconnecting QMT read boundary with no write operations."""

    def __init__(self, settings: GatewaySettings) -> None:
        self.settings = settings
        self.trader: Any | None = None
        self.account: Any | None = None
        self.constants: Any | None = None
        self._lock = RLock()
        self._reconnect = False
        self._connection_generation: int | None = None
        self._next_generation = 0
        self._last_reconnect_session_id = settings.session_id
        self._retired_traders: list[Any] = []

    def _import_xtquant(self) -> tuple[Any, Any, Any, Any]:
        site_packages = self.settings.xtquant_site_packages
        if site_packages is not None:
            if not site_packages.is_dir():
                raise RuntimeError("QMT_XTQUANT_SITE_PACKAGES is not a directory")
            path = str(site_packages)
            if path not in sys.path:
                sys.path.insert(0, path)
        try:
            from xtquant import xtconstant
            from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
            from xtquant.xttype import StockAccount
        except ImportError as exc:
            raise RuntimeError("xtquant is not available in the gateway environment") from exc
        return xtconstant, XtQuantTrader, XtQuantTraderCallback, StockAccount

    def _reconnect_session_id(self) -> int:
        """Return a session ID distinct from every previous reconnect in this process."""
        candidate = int(datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H%M%S%f")[:-3])
        self._last_reconnect_session_id = max(candidate, self._last_reconnect_session_id + 1)
        return self._last_reconnect_session_id

    def _invalidate_locked(self) -> Any | None:
        trader = self.trader
        self.trader = None
        self.account = None
        self.constants = None
        self._connection_generation = None
        self._reconnect = True
        return trader

    def _stop_retired_traders_locked(self) -> None:
        retired_traders, self._retired_traders = self._retired_traders, []
        for trader in retired_traders:
            try:
                trader.stop()
            except Exception:
                LOGGER.debug("MiniQMT trader stop failed during disconnect cleanup", exc_info=True)

    def _disconnect_callback(self, generation: int) -> None:
        with self._lock:
            if generation != self._connection_generation:
                LOGGER.debug("Ignoring disconnect callback from retired MiniQMT session %s", generation)
                return
            trader = self._invalidate_locked()
            if trader is not None:
                self._retired_traders.append(trader)
        LOGGER.warning("MiniQMT disconnected; the next read will reconnect with a new session")

    def ensure_connected(self) -> None:
        with self._lock:
            if self.trader is not None and self.account is not None:
                return
            self._stop_retired_traders_locked()
            if not self.settings.userdata_path.is_dir():
                raise RuntimeError("QMT_USERDATA_PATH does not exist or is not a directory")
            constants, trader_class, callback_class, account_class = self._import_xtquant()
            session_id = self._reconnect_session_id() if self._reconnect else self.settings.session_id
            self._next_generation += 1
            generation = self._next_generation
            owner = self

            class GatewayCallback(callback_class):
                def on_disconnected(self) -> None:
                    owner._disconnect_callback(generation)

            trader = trader_class(str(self.settings.userdata_path), session_id)
            try:
                trader.register_callback(GatewayCallback())
                trader.start()
                if trader.connect() != 0:
                    raise RuntimeError("MiniQMT connection failed")
                account = account_class(self.settings.account_id, account_type=self.settings.account_type)
                if trader.subscribe(account) != 0:
                    raise RuntimeError("MiniQMT account subscription failed")
            except Exception:
                try:
                    trader.stop()
                except Exception:
                    LOGGER.debug("MiniQMT trader stop failed during connection cleanup", exc_info=True)
                self._invalidate_locked()
                raise
            self.trader, self.account, self.constants = trader, account, constants
            self._connection_generation = generation
            self._reconnect = True

    def _query(self, operation: Callable[[Any, Any], T]) -> T:
        with self._lock:
            self.ensure_connected()
            if self.trader is None or self.account is None:
                raise RuntimeError("MiniQMT is not connected")
            try:
                return operation(self.trader, self.account)
            except Exception as exc:
                stale = self._invalidate_locked()
                try:
                    if stale is not None:
                        stale.stop()
                except Exception:
                    LOGGER.debug("MiniQMT trader stop failed during query cleanup", exc_info=True)
                raise RuntimeError("QMT read query failed") from exc

    def _side(self, order_type: Any) -> str:
        if self.constants is None:
            raise RuntimeError("QMT constants are unavailable")
        if order_type == self.constants.STOCK_BUY:
            return "BUY"
        if order_type == self.constants.STOCK_SELL:
            return "SELL"
        raise ValueError(f"unsupported QMT stock order type: {order_type!r}")

    def query_asset(self) -> QmtAsset:
        asset = self._query(lambda trader, account: trader.query_stock_asset(account))
        if asset is None:
            raise RuntimeError("QMT did not return an account asset snapshot")
        return QmtAsset(total_asset=float(_value(asset, "total_asset")), cash=float(_value(asset, "cash")))

    def query_positions(self) -> Sequence[QmtPosition]:
        items = self._query(lambda trader, account: trader.query_stock_positions(account))
        return [
            QmtPosition(
                stock_code=str(_value(item, "stock_code")),
                volume=float(_value(item, "volume")),
                can_use_volume=float(_value(item, "can_use_volume")),
            )
            for item in (items or [])
        ]

    def query_orders(self) -> Sequence[QmtOrder]:
        items = self._query(lambda trader, account: trader.query_stock_orders(account))
        result: list[QmtOrder] = []
        for item in items or []:
            raw_status = _value(item, "order_status")
            try:
                status = _ORDER_STATUS[int(raw_status)]
            except (KeyError, TypeError, ValueError):
                # QMT may add statuses between client upgrades. Preserve the raw
                # value for reconciliation instead of making the entire snapshot
                # unavailable; downstream systems must treat UNKNOWN as non-final.
                LOGGER.warning("Unknown QMT order status %r; exposing it as UNKNOWN", raw_status)
                status = "UNKNOWN"
            result.append(
                QmtOrder(
                    order_id=str(_value(item, "order_id")),
                    stock_code=str(_value(item, "stock_code")),
                    side=self._side(_value(item, "order_type")),
                    order_volume=float(_value(item, "order_volume")),
                    traded_volume=float(_value(item, "traded_volume")),
                    price=float(_value(item, "price")),
                    status=status,
                    event_at_utc=_event_time(_value(item, "order_time")),
                    raw_status=raw_status,
                )
            )
        return result

    def query_fills(self) -> Sequence[QmtFill]:
        items = self._query(lambda trader, account: trader.query_stock_trades(account))
        result: list[QmtFill] = []
        for item in items or []:
            result.append(
                QmtFill(
                    trade_id=str(_value(item, "traded_id")),
                    order_id=str(_value(item, "order_id")),
                    stock_code=str(_value(item, "stock_code")),
                    side=self._side(_value(item, "order_type")),
                    traded_volume=float(_value(item, "traded_volume")),
                    traded_price=float(_value(item, "traded_price")),
                    event_at_utc=_event_time(_value(item, "traded_time")),
                )
            )
        return result

    def close(self) -> None:
        with self._lock:
            trader = self._invalidate_locked()
            if trader is not None:
                trader.stop()
            self._stop_retired_traders_locked()
