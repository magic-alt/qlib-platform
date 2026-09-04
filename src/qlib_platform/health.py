from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from typing import Any

from .data_source_resolver import ReleaseSelectionRequired, resolve_source
from .settings import Settings
from .standalone_status import collect_status


def live_health() -> dict[str, str]:
    return {"status": "live"}


def _registry_health(settings: Settings) -> tuple[str, str]:
    path = settings.registry_path
    if not path.is_file():
        return "ready", "uninitialized"
    try:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5) as connection:
            result = connection.execute("PRAGMA quick_check").fetchone()
            if result is None or str(result[0]).lower() != "ok":
                return "unavailable", "integrity_check_failed"
            tables = {
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
        required = {"dataset_versions", "data_releases", "release_aliases"}
        if not required.issubset(tables):
            return "unavailable", "schema_incomplete"
        return "ready", "integrity_ok"
    except sqlite3.Error:
        return "unavailable", "database_error"


def _filesystem_health(settings: Settings) -> tuple[str, str, int | None]:
    probe_root = settings.paths.root if settings.paths.root.is_dir() else settings.paths.root.parent
    if not probe_root.is_dir():
        return "unavailable", "root_parent_missing", None
    health_cfg = settings.data.get("health", {})
    configured = health_cfg.get("min_free_bytes") if isinstance(health_cfg, dict) else None
    minimum_free = int(configured) if configured is not None else 100 * 1024 * 1024
    try:
        free = shutil.disk_usage(probe_root).free
        if free < minimum_free:
            return "unavailable", "insufficient_free_space", free
        descriptor, source_name = tempfile.mkstemp(prefix=".health-", dir=probe_root)
        os.close(descriptor)
        source = os.path.abspath(source_name)
        target = source + ".renamed"
        try:
            os.replace(source, target)
        finally:
            for candidate in (source, target):
                try:
                    os.unlink(candidate)
                except FileNotFoundError:
                    pass
        return "ready", "writable_atomic_rename", free
    except OSError:
        return "unavailable", "write_or_rename_failed", None


def ready_health(settings: Settings) -> dict[str, Any]:
    from .auth import local_auth_backend

    auth = local_auth_backend(settings.paths.root).status()
    registry, registry_detail = _registry_health(settings)
    filesystem, filesystem_detail, free_bytes = _filesystem_health(settings)
    ready = auth != "unavailable" and registry == "ready" and filesystem == "ready"
    return {
        "status": "ready" if ready else "not_ready",
        "configuration": "ready",
        "auth": auth,
        "registry": registry,
        "filesystem": filesystem,
        "checks": {
            "registry": registry_detail,
            "filesystem": filesystem_detail,
            "filesystemFreeBytes": free_bytes,
        },
    }


def dependency_health(settings: Settings) -> dict[str, Any]:
    try:
        status = collect_status(settings)
    except (OSError, sqlite3.Error, ValueError):
        status = {"tushare": "unknown", "platform": "unknown"}
    try:
        source = resolve_source(settings)
        local_data = "healthy" if source.status == "READY" else source.status.lower()
    except ReleaseSelectionRequired:
        local_data = "selection_required"
    except (OSError, sqlite3.Error, ValueError):
        local_data = "unhealthy"
    return {
        "local_data": local_data,
        "tushare": status["tushare"],
        "platform": status["platform"],
        "execution_export": ("healthy" if status["platform"] == "healthy" else "degraded"),
    }
