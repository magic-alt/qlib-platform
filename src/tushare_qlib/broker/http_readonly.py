from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, cast
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from .base import BrokerSnapshot, validate_broker_snapshot


HttpTransport = Callable[[Request, float], bytes]


def _default_transport(request: Request, timeout: float) -> bytes:
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - configured internal gateway
        return cast(bytes, response.read())


class ReadOnlyJsonClient:
    """GET-only JSON client for user-operated broker and quote gateways."""

    def __init__(
        self,
        base_url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 10.0,
        max_attempts: int = 3,
        retry_delay_seconds: float = 0.25,
        transport: HttpTransport | None = None,
    ) -> None:
        if not base_url.lower().startswith(("http://", "https://")):
            raise ValueError("read-only gateway base_url must use http or https")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.base_url = base_url.rstrip("/")
        self.headers = dict(headers or {})
        self.timeout_seconds = float(timeout_seconds)
        self.max_attempts = int(max_attempts)
        self.retry_delay_seconds = float(retry_delay_seconds)
        self.transport = transport or _default_transport

    def get(self, path: str, **query: object) -> Any:
        suffix = urlencode({key: value for key, value in query.items() if value is not None})
        url = f"{self.base_url}/{path.lstrip('/')}"
        if suffix:
            url = f"{url}?{suffix}"
        request = Request(url, headers=self.headers, method="GET")
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                payload = json.loads(self.transport(request, self.timeout_seconds).decode("utf-8"))
                if isinstance(payload, Mapping) and "data" in payload:
                    payload = payload["data"]
                return payload
            except (OSError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt + 1 < self.max_attempts:
                    time.sleep(self.retry_delay_seconds)
        raise ConnectionError("read-only gateway request failed after retries") from last_error


@dataclass(frozen=True)
class BrokerEndpoints:
    account: str = "account"
    positions: str = "positions"
    orders: str = "orders"
    fills: str = "fills"


class HttpReadOnlyBrokerAdapter:
    source_name = "http_readonly"

    def __init__(self, client: ReadOnlyJsonClient, endpoints: BrokerEndpoints | None = None) -> None:
        self.client = client
        self.endpoints = endpoints or BrokerEndpoints()

    @staticmethod
    def _frame(payload: Any, name: str) -> pd.DataFrame:
        if payload is None:
            return pd.DataFrame()
        if isinstance(payload, Mapping):
            payload = payload.get(name, payload.get("items", payload))
        if isinstance(payload, Mapping):
            payload = [payload]
        if not isinstance(payload, list):
            raise ValueError(f"broker {name} response must be a JSON list")
        return pd.DataFrame(payload)

    def snapshot(self, trade_date: str) -> BrokerSnapshot:
        account = self.client.get(self.endpoints.account, trade_date=trade_date)
        if not isinstance(account, Mapping):
            raise ValueError("broker account response must be a JSON object")
        positions = self._frame(
            self.client.get(self.endpoints.positions, trade_date=trade_date), "positions"
        )
        if positions.empty and not len(positions.columns):
            positions = pd.DataFrame(
                columns=[
                    "instrument",
                    "quantity",
                    "available_quantity",
                    "as_of_trade_date",
                    "snapshot_at_utc",
                ]
            )
        initial_holdings = (
            positions.loc[:, ["instrument", "opened_trade_date"]].copy()
            if "opened_trade_date" in positions.columns
            else None
        )
        snapshot = BrokerSnapshot(
            account=dict(account),
            positions=positions,
            orders=self._frame(self.client.get(self.endpoints.orders, trade_date=trade_date), "orders"),
            fills=self._frame(self.client.get(self.endpoints.fills, trade_date=trade_date), "fills"),
            initial_holdings=initial_holdings,
        )
        return validate_broker_snapshot(snapshot, trade_date)
