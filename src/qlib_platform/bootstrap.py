from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from qlib_platform.datasets.data_source_resolver import (
    ReleaseSelectionRequired,
    resolve_local_raw_source,
    resolve_source,
)
from qlib_platform.releases import import_qlib_dataset, publish_local_market_release
from qlib_platform.settings import Settings


def _run_cli(settings: Settings, *args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "qlib_platform", "--config", str(settings.config_path), *args],
        check=True,
    )


def _recover_selected_dataset_alias(settings: Settings, release_id: str) -> dict[str, Any] | None:
    """Recover a missing DatasetVersion alias after an explicit release selection.

    This deliberately does not guess between historical DataReleases. Recovery is only
    allowed when ``research-release-current`` already points at ``release_id`` and one
    usable DatasetVersion is bound to that release for the configured dataset name.
    """

    from qlib_platform.datasets.dataset_registry import DatasetRegistry

    registry = DatasetRegistry(settings.registry_path)
    if registry.resolve_release_alias("research-release-current") != release_id:
        return None
    candidates = [
        item
        for item in registry.list_versions(settings.qlib_dataset_name)
        if item.data_release_id == release_id
        and item.status in {"VALIDATED", "PUBLISHED"}
        and item.manifest_path.is_file()
        and item.data_path.is_dir()
    ]
    if len(candidates) != 1:
        return None
    dataset = candidates[0]
    registry.promote_research_snapshot(
        release_alias="research-release-current",
        data_release_id=release_id,
        dataset_alias=settings.qlib_dataset_ref,
        dataset_version_id=dataset.version_id,
    )
    return {
        "status": "READY",
        "source": "dataset_version",
        "reference": settings.qlib_dataset_ref,
        "dataReleaseId": release_id,
        "datasetVersionId": dataset.version_id,
        "aliasRecovered": True,
    }


def bootstrap(
    settings: Settings,
    *,
    source: str = "auto",
    path: str | Path | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    if source == "auto":
        try:
            resolved = resolve_source(settings)
        except ReleaseSelectionRequired as exc:
            return {
                "status": exc.code,
                "error": str(exc),
                "recommendedCommand": "tq release list",
                "selectionCommand": ("tq release promote <DATA_RELEASE_ID> --alias research-release-current"),
                "datasetRecoveryCommand": f"tq registry-rebuild --root {settings.paths.root}",
                "retryCommand": "tq-research prepare --source auto",
            }
        if resolved.status == "MATERIALIZE_REQUIRED" and resolved.reference:
            recovered = _recover_selected_dataset_alias(settings, resolved.reference)
            if recovered is not None:
                return recovered
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
        # Provider credential validation belongs to the source adapter/registry;
        # init-metadata resolves the provider before any ingestion write occurs.
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
