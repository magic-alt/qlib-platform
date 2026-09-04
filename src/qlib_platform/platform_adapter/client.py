from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from qlib_platform.platform_adapter.outbox import OutboxItem


@dataclass(frozen=True)
class PlatformClient:
    """Minimal Artifact Contract v2 delivery client with idempotent request identity."""

    endpoint: str
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        parsed = urlparse(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Platform artifact endpoint must be an absolute HTTP(S) URL")
        if self.timeout_seconds <= 0:
            raise ValueError("Platform client timeout must be positive")

    def send(self, item: OutboxItem) -> None:
        payload = json.loads(item.artifact_path.read_text(encoding="utf-8"))
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": item.item_id,
                "X-Artifact-SHA256": item.artifact_sha256,
                "X-Data-Release-ID": item.data_release_id,
            },
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
            if not 200 <= response.status < 300:
                raise ConnectionError(f"Platform artifact delivery returned HTTP {response.status}")
