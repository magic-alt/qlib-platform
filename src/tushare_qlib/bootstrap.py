from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from .data_source_resolver import resolve_source
from .releases import import_qlib_dataset
from .settings import Settings


def _run_cli(settings: Settings, *args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "tushare_qlib", "--config", str(settings.config_path), *args],
        check=True,
    )


def bootstrap(
    settings: Settings,
    *,
    source: str = "auto",
    path: str | Path | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    if source == "auto":
        resolved = resolve_source(settings)
        if resolved.status == "IMPORT_REQUIRED":
            release, dataset = import_qlib_dataset(settings, resolved.path or settings.qlib_data_uri)
            return {
                "status": "READY",
                "source": "qlib",
                "dataReleaseId": release.data_release_id,
                "datasetVersionId": dataset.version_id,
            }
        if resolved.status == "BUILD_REQUIRED":
            _run_cli(settings, "dataset-build")
            resolved = resolve_source(settings)
        return {
            "status": resolved.status,
            "source": resolved.source,
            "reference": resolved.reference,
            "action": resolved.action,
        }
    if source == "qlib":
        if path is None:
            path = settings.qlib_data_uri
        release, dataset = import_qlib_dataset(settings, path)
        return {
            "status": "READY",
            "source": "qlib",
            "dataReleaseId": release.data_release_id,
            "datasetVersionId": dataset.version_id,
        }
    if source == "raw":
        _run_cli(
            settings,
            "dataset-build",
            *(["--start", start] if start else []),
            *(["--end", end] if end else []),
        )
        return {"status": "READY", "source": "local_raw"}
    if source == "tushare":
        if not start or not end:
            raise ValueError("TuShare bootstrap requires explicit --start and --end")
        settings.require_token()
        _run_cli(settings, "init-metadata")
        _run_cli(settings, "backfill", "--start", start, "--end", end)
        _run_cli(settings, "backfill-extended", "--start", start, "--end", end)
        _run_cli(settings, "sync-dividends", "--bootstrap")
        _run_cli(settings, "sync-universe", "--start", start, "--end", end)
        _run_cli(settings, "sync-benchmark", "--start", start, "--end", end)
        _run_cli(settings, "sync-industry", "--end", end)
        _run_cli(settings, "dataset-build", "--start", start, "--end", end)
        return {"status": "READY", "source": "tushare"}
    raise ValueError(f"unsupported bootstrap source: {source}")
