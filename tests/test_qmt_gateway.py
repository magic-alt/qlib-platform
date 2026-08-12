from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from fastapi.testclient import TestClient

from tushare_qlib.qmt_gateway import GatewaySettings, create_app
from tushare_qlib.qmt_gateway.models import QmtAsset, QmtFill, QmtOrder, QmtPosition
from tushare_qlib.qmt_gateway.nav_store import NavStore
from tushare_qlib.qmt_gateway.xtquant_client import _event_time, qmt_to_qlib_symbol


class FakeQmtClient:
    def __init__(self) -> None:
        self.connect_count = 0

    def ensure_connected(self) -> None:
        self.connect_count += 1

    def query_asset(self) -> QmtAsset:
        return QmtAsset(total_asset=1_150.0, cash=350.0)

    def query_positions(self) -> list[QmtPosition]:
        return [QmtPosition("600000.SH", 100.0, 100.0)]

    def query_orders(self) -> list[QmtOrder]:
        return [
            QmtOrder(
                order_id="order-1",
                stock_code="600000.SH",
                side="BUY",
                order_volume=100.0,
                traded_volume=20.0,
                price=10.5,
                status="PARTIALLY_FILLED",
                event_at_utc="2026-08-12T01:31:00Z",
                raw_status=55,
            )
        ]

    def query_fills(self) -> list[QmtFill]:
        return [
            QmtFill(
                trade_id="trade-1",
                order_id="order-1",
                stock_code="600000.SH",
                side="BUY",
                traded_volume=20.0,
                traded_price=10.5,
                event_at_utc="2026-08-12T01:31:00Z",
            )
        ]


def _trade_date() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def _app(tmp_path: Path) -> tuple[TestClient, FakeQmtClient]:
    settings = GatewaySettings(
        userdata_path=tmp_path / "userdata_mini",
        account_id="test-account",
        account_type="STOCK",
        session_id=18001,
        token="test-token",
        state_dir=tmp_path / "state",
    )
    store = NavStore(settings.state_dir)
    store.capture("2000-01-01", 1_000.0)
    fake = FakeQmtClient()
    return TestClient(create_app(settings, client=fake, nav_store=store)), fake


def test_gateway_requires_bearer_token_and_returns_qlib_snapshot(tmp_path: Path):
    client, fake = _app(tmp_path)
    trade_date = _trade_date()

    assert client.get("/v1/account", params={"trade_date": trade_date}).status_code == 401
    account = client.get(
        "/v1/account",
        params={"trade_date": trade_date},
        headers={"Authorization": "Bearer test-token"},
    )
    assert account.status_code == 200
    assert account.json()["portfolio_value"] == 1150.0
    assert account.json()["cash"] == 350.0
    assert account.json()["daily_pnl_pct"] == 0.15
    assert fake.connect_count >= 1


def test_gateway_maps_qmt_positions_orders_and_fills(tmp_path: Path):
    client, _ = _app(tmp_path)
    headers = {"Authorization": "Bearer test-token"}
    query = {"trade_date": _trade_date()}

    positions = client.get("/v1/positions", params=query, headers=headers)
    orders = client.get("/v1/orders", params=query, headers=headers)
    fills = client.get("/v1/fills", params=query, headers=headers)

    assert positions.json()[0]["instrument"] == "SH600000"
    assert positions.json()[0]["available_quantity"] == 100.0
    assert orders.json()[0]["status"] == "PARTIALLY_FILLED"
    assert orders.json()[0]["qmt_order_status"] == 55
    assert fills.json()[0]["fill_id"] == "trade-1"


def test_gateway_rejects_historical_trade_date_and_missing_nav(tmp_path: Path):
    client, _ = _app(tmp_path)
    headers = {"Authorization": "Bearer test-token"}
    historical = client.get("/v1/positions", params={"trade_date": "2000-01-01"}, headers=headers)
    assert historical.status_code == 422

    settings = GatewaySettings(
        userdata_path=tmp_path / "userdata_mini",
        account_id="test-account",
        account_type="STOCK",
        session_id=18001,
        token="test-token",
        state_dir=tmp_path / "empty-state",
    )
    missing_nav = TestClient(create_app(settings, client=FakeQmtClient())).get(
        "/v1/account", params={"trade_date": _trade_date()}, headers=headers
    )
    assert missing_nav.status_code == 503


def test_nav_store_adjusts_external_cash_flows(tmp_path: Path):
    store = NavStore(tmp_path)
    store.capture("2026-08-11", 1_000.0)
    store.record_cash_flow("2026-08-12", 100.0, "deposit")

    assert store.daily_pnl_pct("2026-08-12", 1_150.0) == 0.05


def test_symbol_and_qmt_timestamp_mapping_are_strict():
    assert qmt_to_qlib_symbol("600000.SH") == "SH600000"
    assert qmt_to_qlib_symbol("000001.SZ") == "SZ000001"
    assert _event_time(20260812103015) == "2026-08-12T02:30:15Z"


def test_static_openapi_describes_all_read_only_endpoints():
    path = Path("src/tushare_qlib/qmt_gateway/openapi.yaml")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert document["openapi"] == "3.1.0"
    assert set(document["paths"]) == {
        "/v1/health",
        "/v1/account",
        "/v1/positions",
        "/v1/orders",
        "/v1/fills",
    }
    assert all(set(value) == {"get"} for value in document["paths"].values())
