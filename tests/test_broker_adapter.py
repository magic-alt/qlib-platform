from __future__ import annotations

import json
import pytest

from tushare_qlib.broker import HttpReadOnlyBrokerAdapter, ReadOnlyJsonClient


def _payload(url: str) -> object:
    if "/account?" in url:
        return {
            "as_of_trade_date": "2026-08-11",
            "snapshot_at_utc": "2026-08-11T00:59:00Z",
            "portfolio_value": 100000,
            "cash": 50000,
            "daily_pnl_pct": 0.0,
        }
    if "/positions?" in url:
        return [
            {
                "instrument": "SH600000",
                "quantity": 100,
                "available_quantity": 100,
                "as_of_trade_date": "2026-08-11",
                "snapshot_at_utc": "2026-08-11T00:59:00Z",
            }
        ]
    return []


def test_http_broker_adapter_reconnects_and_is_read_only():
    attempts = 0

    def transport(request, timeout):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("temporary gateway timeout")
        return json.dumps(_payload(request.full_url)).encode()

    adapter = HttpReadOnlyBrokerAdapter(
        ReadOnlyJsonClient(
            "https://broker.invalid/v1",
            max_attempts=2,
            retry_delay_seconds=0,
            transport=transport,
        )
    )

    snapshot = adapter.snapshot("2026-08-11")

    assert snapshot.account["cash"] == 50000
    assert snapshot.positions["instrument"].tolist() == ["SH600000"]
    assert attempts == 5
    assert not hasattr(adapter, "submit_order")
    assert not hasattr(adapter, "cancel_order")


def test_http_broker_adapter_rejects_partial_account():
    def transport(request, timeout):
        payload = [] if "/account?" not in request.full_url else {"cash": 1}
        return json.dumps(payload).encode()

    adapter = HttpReadOnlyBrokerAdapter(
        ReadOnlyJsonClient("https://broker.invalid", transport=transport)
    )

    with pytest.raises(ValueError, match="account snapshot missing fields"):
        adapter.snapshot("2026-08-11")


def test_http_broker_adapter_rejects_wrong_trade_date():
    def transport(request, timeout):
        value = _payload(request.full_url)
        if isinstance(value, dict):
            value["as_of_trade_date"] = "2026-08-10"
        return json.dumps(value).encode()

    adapter = HttpReadOnlyBrokerAdapter(
        ReadOnlyJsonClient("https://broker.invalid", transport=transport)
    )

    with pytest.raises(ValueError, match="trade date"):
        adapter.snapshot("2026-08-11")
