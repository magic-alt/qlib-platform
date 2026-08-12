from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .nav_store import NavStore
from .xtquant_client import QmtReadOnlyClient, qmt_to_qlib_symbol


class QmtGatewayService:
    def __init__(self, client: QmtReadOnlyClient, nav_store: NavStore) -> None:
        self.client = client
        self.nav_store = nav_store

    @staticmethod
    def _trade_date(value: str) -> date:
        requested = date.fromisoformat(value)
        current = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        if requested != current:
            raise ValueError("QMT gateway only supports the current Asia/Shanghai trade date")
        return requested

    @staticmethod
    def _snapshot_at_utc() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def health(self) -> dict[str, object]:
        try:
            self.client.ensure_connected()
        except Exception as exc:
            return {"status": "degraded", "reason": str(exc), "qmt_connected": False}
        return {"status": "ready", "qmt_connected": True}

    def account(self, trade_date: str) -> dict[str, object]:
        requested = self._trade_date(trade_date)
        self.client.ensure_connected()
        asset = self.client.query_asset()
        return {
            "as_of_trade_date": requested.isoformat(),
            "snapshot_at_utc": self._snapshot_at_utc(),
            "portfolio_value": asset.total_asset,
            "cash": asset.cash,
            "daily_pnl_pct": self.nav_store.daily_pnl_pct(requested.isoformat(), asset.total_asset),
        }

    def positions(self, trade_date: str) -> list[dict[str, object]]:
        requested = self._trade_date(trade_date)
        self.client.ensure_connected()
        snapshot_at = self._snapshot_at_utc()
        return [
            {
                "instrument": qmt_to_qlib_symbol(item.stock_code),
                "quantity": item.volume,
                "available_quantity": item.can_use_volume,
                "as_of_trade_date": requested.isoformat(),
                "snapshot_at_utc": snapshot_at,
            }
            for item in self.client.query_positions()
        ]

    def orders(self, trade_date: str) -> list[dict[str, object]]:
        requested = self._trade_date(trade_date)
        self.client.ensure_connected()
        snapshot_at = self._snapshot_at_utc()
        return [
            {
                "broker_order_id": item.order_id,
                "instrument": qmt_to_qlib_symbol(item.stock_code),
                "side": item.side,
                "quantity": item.order_volume,
                "filled_quantity": item.traded_volume,
                "limit_price": item.price,
                "status": item.status,
                "event_at_utc": item.event_at_utc,
                "qmt_order_status": item.raw_status,
                "as_of_trade_date": requested.isoformat(),
                "snapshot_at_utc": snapshot_at,
            }
            for item in self.client.query_orders()
        ]

    def fills(self, trade_date: str) -> list[dict[str, object]]:
        requested = self._trade_date(trade_date)
        self.client.ensure_connected()
        snapshot_at = self._snapshot_at_utc()
        return [
            {
                "fill_id": item.trade_id,
                "broker_order_id": item.order_id,
                "instrument": qmt_to_qlib_symbol(item.stock_code),
                "side": item.side,
                "quantity": item.traded_volume,
                "price": item.traded_price,
                "event_at_utc": item.event_at_utc,
                "as_of_trade_date": requested.isoformat(),
                "snapshot_at_utc": snapshot_at,
            }
            for item in self.client.query_fills()
        ]
