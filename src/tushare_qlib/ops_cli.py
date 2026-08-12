from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .ops_state import OpsState
from .settings import Settings


def state_from_settings(settings: Settings) -> OpsState:
    return OpsState(settings.paths.state / "ops.sqlite3")


def query_ops(
    settings: Settings,
    *,
    entity: str,
    business_date: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    state = state_from_settings(settings)
    if entity == "runs":
        return state.list_runs(business_date=business_date, status=status)
    if entity == "deliveries":
        return state.list_deliveries(business_date=business_date, status=status)
    raise ValueError("ops entity must be runs or deliveries")


def export_daily_ops(settings: Settings, business_date: str, output: str | Path) -> Path:
    target = Path(output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = state_from_settings(settings).daily_summary(business_date)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target
