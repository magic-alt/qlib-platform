from __future__ import annotations

from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from tushare_qlib.broker import HttpReadOnlyBrokerAdapter, ReadOnlyJsonClient
from tushare_qlib.qmt_gateway import GatewaySettings, create_app
from tushare_qlib.qmt_gateway.models import QmtAsset
from tushare_qlib.qmt_gateway.nav_store import NavStore


class EmptyQmtClient:
    def ensure_connected(self) -> None: pass

    def query_asset(self) -> QmtAsset:
        return QmtAsset(total_asset=100_000.0, cash=40_000.0)

    def query_positions(self): return []

    def query_orders(self): return []

    def query_fills(self): return []


def test_existing_http_adapter_consumes_qmt_gateway_payloads(tmp_path: Path):
    today = datetime.now().date().isoformat()
    settings = GatewaySettings(tmp_path, "test-account", "STOCK", 18001, "test-token", tmp_path / "state")
    store = NavStore(settings.state_dir)
    store.capture("2000-01-01", 100_000.0)
    gateway = TestClient(create_app(settings, client=EmptyQmtClient(), nav_store=store))

    def transport(request, timeout):
        target = urlsplit(request.full_url)
        path = target.path + (f"?{target.query}" if target.query else "")
        response = gateway.get(path, headers=dict(request.header_items()))
        response.raise_for_status()
        return response.content

    adapter = HttpReadOnlyBrokerAdapter(
        ReadOnlyJsonClient("http://gateway/v1", headers={"Authorization": "Bearer test-token"}, transport=transport)
    )
    snapshot = adapter.snapshot(today)

    assert snapshot.account["portfolio_value"] == 100_000.0
    assert snapshot.account["daily_pnl_pct"] == 0.0
    assert snapshot.positions.empty
    assert snapshot.orders.empty
    assert snapshot.fills.empty
