from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from qlib_platform.datasets.data_release import DataReleaseStore
from qlib_platform.datasets.dataset_resolver import pin_dataset
from qlib_platform.releases.capabilities import require_release_capability
from qlib_platform.settings import Settings


def _run_cli(settings: Settings, *args: str) -> None:
    from qlib_platform.cli import main

    argv = ["tq", "--config", str(settings.config_path), *args]
    original = sys.argv
    try:
        sys.argv = argv
        main()
    finally:
        sys.argv = original


def _git_clean(root: Path) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0 and not completed.stdout.strip()


def _resolve_local_release(settings: Settings, start: str | None, end: str | None):
    from qlib_platform.datasets.dataset_resolver import resolve_source

    resolved = resolve_source(settings)
    if resolved.kind not in {"dataset", "local"}:
        raise ValueError(f"local bootstrap requires local/dataset source, got {resolved.kind}")
    if resolved.dataset is not None:
        return resolved
    if not start or not end:
        return resolved
    return resolved


def bootstrap(
    settings: Settings,
    *,
    source: str | None = None,
    start: str | None = None,
    end: str | None = None,
    exploratory: bool = False,
) -> dict[str, Any]:
    source = (source or settings.source_kind or "auto").strip().lower()
    if source == "auto":
        source = "local" if settings.qlib_data_uri.exists() else "tushare"

    if source in {"data_release", "platform_release"}:
        require_release_capability(settings, "dataset")
        pinned = pin_dataset(settings)
        return {
            "status": "READY",
            "source": "data_release",
            "datasetVersionId": pinned.version_id,
            "dataReleaseId": pinned.data_release_id,
        }

    if source in {"local", "dataset", "qlib"}:
        from qlib_platform.datasets.data_release import ensure_local_data_release
        from qlib_platform.datasets.dataset_resolver import resolve_source

        resolved = resolve_source(settings)
        selected_start = start or str(settings.data.get("start_date") or "")
        selected_end = end or str(settings.data.get("end_date") or "")
        if resolved.dataset is not None:
            return {
                "status": "READY",
                "source": "local_dataset",
                "datasetVersionId": resolved.dataset.version_id,
            }
        if not selected_start or not selected_end:
            raise ValueError("local bootstrap requires --start/--end when no DatasetVersion is available")
        if exploratory:
            dataset = pin_dataset(settings, reference=None, allow_legacy=True)
            release = ensure_local_data_release(
                settings,
                dataset=dataset,
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
        # Credential validation belongs to the provider adapter/registry. The first
        # ingestion command resolves the configured source and fails closed before
        # performing any data write when its credential is unavailable.
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
