from __future__ import annotations

from pathlib import Path
from typing import Any

from qlib_platform.datasets.dataset_registry import DatasetRegistry
from qlib_platform.settings import Settings


def _provider_ready(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / "calendars" / "day.txt").is_file()
        and (path / "instruments").is_dir()
        and (path / "features").is_dir()
    )


def collect_status(settings: Settings) -> dict[str, Any]:
    from qlib_platform.auth import local_auth_backend

    registered = (
        DatasetRegistry(settings.registry_path).inspect(settings.qlib_dataset_ref)
        if settings.registry_path.is_file()
        else None
    )
    dataset_path = registered.data_path if registered is not None else settings.qlib_data_uri
    dataset_ready = _provider_ready(dataset_path)
    platform = "not_configured"
    if settings.uses_data_release():
        try:
            platform = "healthy" if settings.platform_release_manifest.is_file() else "unavailable"
        except (FileNotFoundError, ValueError):
            platform = "unavailable"
    from qlib_platform.runtime.sre import collect_sre_status

    return {
        "mode": settings.mode,
        "configuration": "ready",
        "auth": local_auth_backend(settings.paths.root).status(),
        "research": "ready" if dataset_ready else "data_unavailable",
        "dataset": {
            "status": "ready" if dataset_ready else "unavailable",
            "versionId": registered.version_id if registered is not None else None,
            "path": str(dataset_path),
        },
        "tushare": "available" if settings.tushare_token else "not_configured",
        "platform": platform,
        "executionExport": "available" if dataset_ready else "unavailable",
        "unavailableCapabilities": (
            ["lean_validation", "qmt", "oms", "broker_execution"] if platform != "healthy" else []
        ),
        "sre": collect_sre_status(settings),
    }


def render_status(payload: dict[str, Any]) -> str:
    dataset = payload["dataset"]
    lines = [
        "qlib-platform status",
        "",
        f"Mode              {payload['mode']}",
        f"Configuration     {payload['configuration'].upper()}",
        f"Auth              {payload['auth'].upper()}",
        f"Research          {payload['research'].upper()}",
        f"Dataset           {dataset['status'].upper()}",
        f"TuShare           {payload['tushare'].upper()}",
        f"Platform          {payload['platform'].upper()}",
        f"SRE               {payload['sre']['status']}",
        f"Recent session    {payload['sre'].get('recent_session') or 'N/A'}",
        f"Active release    {payload['sre'].get('active_release') or 'N/A'}",
        f"SLO version       {payload['sre']['policy'].get('version') or 'N/A'}",
        f"SRE blockers      {', '.join(payload['sre'].get('blocking_reasons', [])) or 'none'}",
    ]
    return "\n".join(lines)
