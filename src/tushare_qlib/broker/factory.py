from __future__ import annotations

import os
from typing import Mapping

from ..settings import Settings
from .base import ReadOnlyBrokerAdapter
from .http_readonly import BrokerEndpoints, HttpReadOnlyBrokerAdapter, ReadOnlyJsonClient
from .inbox import InboxBrokerAdapter


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def broker_adapter_from_settings(settings: Settings) -> ReadOnlyBrokerAdapter:
    production = _mapping(settings.data.get("production"))
    config = _mapping(production.get("broker"))
    kind = str(config.get("kind", "inbox"))
    if kind == "inbox":
        return InboxBrokerAdapter(settings.paths.root / "inbox" / "pretrade")
    if kind != "http_readonly":
        raise ValueError(f"unsupported read-only broker adapter: {kind}")
    token_env = str(config.get("token_env", ""))
    headers = {"Accept": "application/json"}
    if token_env:
        token = os.environ.get(token_env)
        if not token:
            raise RuntimeError(f"configured broker credential variable is missing: {token_env}")
        headers[str(config.get("token_header", "Authorization"))] = f"Bearer {token}"
    endpoint_config = _mapping(config.get("endpoints"))
    endpoints = BrokerEndpoints(
        account=str(endpoint_config.get("account", "account")),
        positions=str(endpoint_config.get("positions", "positions")),
        orders=str(endpoint_config.get("orders", "orders")),
        fills=str(endpoint_config.get("fills", "fills")),
    )
    return HttpReadOnlyBrokerAdapter(
        ReadOnlyJsonClient(
            str(config.get("base_url", "")),
            headers=headers,
            timeout_seconds=float(str(config.get("timeout_seconds", 10))),
            max_attempts=int(str(config.get("max_attempts", 3))),
            retry_delay_seconds=float(str(config.get("retry_delay_seconds", 0.25))),
        ),
        endpoints,
    )
