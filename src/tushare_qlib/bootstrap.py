from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from .data_source_resolver import resolve_local_raw_source, resolve_source
from .releases import import_qlib_dataset, publish_local_market_release
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
            selected_start = start or str(settings.data.get("start_date") or "")
            selected_end = end or str(settings.data.get("end_date") or "")
            if resolved.profile == "ashare_market_import_v1":
                release, dataset = publish_local_market_release(
                    settings,
                    start=selected_start,
                    end=selected_end,
                )
                return {
                    "status": "READY",
                    "source": "local_raw",
                    "profile": release.profile,
                    "governanceLevel": "exploratory",
                    "dataReleaseId": release.data_release_id,
                    "datasetVersionId": dataset.version_id,
                    "missingCertifiedComponents": list(resolved.missing_components),
                }
            _run_cli(
                settings,
                "dataset-build",
                "--start",
                selected_start,
                "--end",
                selected_end,
            )
            resolved = resolve_source(settings)
        if resolved.status == "DOWNLOAD_REQUIRED":
            return bootstrap(settings, source="tushare", start=start, end=end)
        return {
            "status": resolved.status,
            "source": resolved.source,
            "reference": resolved.reference,
            "action": resolved.action,
            "profile": resolved.profile,
            "missingComponents": list(resolved.missing_components),
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
        selected_start = start or str(settings.data.get("start_date") or "")
        selected_end = end or str(settings.data.get("end_date") or "")
        resolved = resolve_local_raw_source(settings)
        if resolved.status == "DATA_INCOMPLETE":
            raise ValueError(
                f"local raw bootstrap is missing components: {list(resolved.missing_components)}"
            )
        if resolved.profile == "ashare_market_import_v1":
            release, dataset = publish_local_market_release(
                settings,
                start=selected_start,
                end=selected_end,
            )
            return {
                "status": "READY",
                "source": "local_raw",
                "profile": release.profile,
                "governanceLevel": "exploratory",
                "dataReleaseId": release.data_release_id,
                "datasetVersionId": dataset.version_id,
                "missingCertifiedComponents": list(resolved.missing_components),
            }
        _run_cli(
            settings,
            "dataset-build",
            "--start",
            selected_start,
            "--end",
            selected_end,
        )
        return {
            "status": "READY",
            "source": "local_raw",
            "profile": "ashare_qlib_research_v2",
            "governanceLevel": "research",
        }
    if source == "tushare":
        start = start or str(settings.data.get("start_date") or "")
        end = end or str(settings.data.get("end_date") or "")
        if not start or not end:
            raise ValueError("TuShare bootstrap requires --start/--end or configured start_date/end_date")
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
