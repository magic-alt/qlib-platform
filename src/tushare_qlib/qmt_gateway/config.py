from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GatewaySettings:
    """Non-secret configuration for the local, read-only QMT gateway."""

    userdata_path: Path
    account_id: str
    account_type: str
    session_id: int
    token: str
    state_dir: Path
    xtquant_site_packages: Path | None = None

    @classmethod
    def from_environment(cls) -> "GatewaySettings":
        def required(name: str) -> str:
            value = os.environ.get(name, "").strip()
            if not value:
                raise RuntimeError(f"required gateway configuration is missing: {name}")
            return value

        try:
            session_id = int(os.environ.get("QMT_SESSION_ID", "18001"))
        except ValueError as exc:
            raise RuntimeError("QMT_SESSION_ID must be an integer") from exc
        if session_id <= 0:
            raise RuntimeError("QMT_SESSION_ID must be positive")
        return cls(
            userdata_path=Path(required("QMT_USERDATA_PATH")),
            account_id=required("QMT_ACCOUNT_ID"),
            account_type=os.environ.get("QMT_ACCOUNT_TYPE", "STOCK").strip() or "STOCK",
            session_id=session_id,
            token=required("QMT_GATEWAY_TOKEN"),
            state_dir=Path(os.environ.get("QMT_GATEWAY_STATE_DIR", "data/qmt_gateway")),
            xtquant_site_packages=(
                Path(os.environ["QMT_XTQUANT_SITE_PACKAGES"])
                if os.environ.get("QMT_XTQUANT_SITE_PACKAGES", "").strip()
                else None
            ),
        )
