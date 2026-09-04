from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from qlib_platform.notifier.models import NotificationEnvelope


class NotificationDeliveryError(RuntimeError):
    pass


class FeishuNotifier:
    channel = "feishu"

    def __init__(
        self,
        webhook_url: str,
        *,
        secret: str | None = None,
        timeout_seconds: float = 10.0,
        transport: Callable[[Request, float], bytes] | None = None,
    ) -> None:
        if not webhook_url.strip():
            raise ValueError("FEISHU_WEBHOOK_URL is required")
        self._webhook_url = webhook_url
        self._secret = secret
        self.timeout_seconds = timeout_seconds
        self._transport = transport or self._default_transport

    @staticmethod
    def _default_transport(request: Request, timeout: float) -> bytes:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - configured Feishu endpoint.
            return bytes(response.read())

    def _signed_payload(self, envelope: NotificationEnvelope) -> dict[str, object]:
        payload = envelope.to_feishu_card()
        if self._secret:
            timestamp = str(int(time.time()))
            sign_data = f"{timestamp}\n{self._secret}".encode()
            signature = base64.b64encode(hmac.new(sign_data, digestmod=hashlib.sha256).digest()).decode()
            payload["timestamp"] = timestamp
            payload["sign"] = signature
        return payload

    def send(self, envelope: NotificationEnvelope) -> None:
        body = json.dumps(self._signed_payload(envelope), ensure_ascii=False).encode("utf-8")
        request = Request(
            self._webhook_url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            response = json.loads(self._transport(request, self.timeout_seconds).decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NotificationDeliveryError(type(exc).__name__) from exc
        code = response.get("code", response.get("StatusCode", -1))
        if int(code) != 0:
            raise NotificationDeliveryError(f"FEISHU_RESPONSE_{code}")
