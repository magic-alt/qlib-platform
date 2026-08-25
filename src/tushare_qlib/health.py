from __future__ import annotations

from typing import Any

from .data_source_resolver import ReleaseSelectionRequired, resolve_source
from .settings import Settings
from .standalone_status import collect_status


def live_health() -> dict[str, str]:
    return {"status": "live"}


def ready_health(settings: Settings) -> dict[str, Any]:
    status = collect_status(settings)
    ready = status["configuration"] == "ready" and status["auth"] != "unavailable"
    return {
        "status": "ready" if ready else "not_ready",
        "configuration": status["configuration"],
        "auth": status["auth"],
        "registry": "ready",
        "filesystem": "ready" if settings.paths.root.parent.exists() else "unavailable",
    }


def dependency_health(settings: Settings) -> dict[str, Any]:
    status = collect_status(settings)
    try:
        source = resolve_source(settings)
        local_data = "healthy" if source.status == "READY" else source.status.lower()
    except ReleaseSelectionRequired:
        local_data = "selection_required"
    return {
        "local_data": local_data,
        "tushare": status["tushare"],
        "platform": status["platform"],
        "execution_export": ("healthy" if status["platform"] == "healthy" else "degraded"),
    }
