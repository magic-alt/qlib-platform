from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

from qlib_platform.datasets.data_source_resolver import (
    ReleaseSelectionRequired,
    resolve_local_raw_source,
    resolve_source,
)
from qlib_platform.releases import (
    FileReleaseStore,
    import_qlib_dataset,
    publish_local_market_release,
    release_store_root,
)
from qlib_platform.settings import Settings


def _run_cli(settings: Settings, *args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "qlib_platform", "--config", str(settings.config_path), *args],
        check=True,
    )


def _archive_standalone_history(settings: Settings, release_id: str | None) -> int:
    if settings.mode != "standalone" or not release_id:
        return 0
    store_cfg = settings.data.get("release_store", {})
    keep_active = int(store_cfg.get("active_keep", 1)) if isinstance(store_cfg, dict) else 1
    if keep_active != 1:
        # Integrated/history-heavy workflows remain opt-in. Standalone supports the
        # zero-config one-active-snapshot policy requested by the quickstart contract.
        return 0
    from qlib_platform.datasets.dataset_registry import DatasetRegistry

    store = FileReleaseStore(release_store_root(settings))
    archived = store.archive_except(release_id)
    if archived:
        registry = DatasetRegistry(settings.registry_path)
        for archived_id in archived:
            release = store.resolve(archived_id, mode="manifest")
            policies = release.manifest.get("policies", {})
            governance = (
                str(policies.get("governanceLevel") or "research")
                if isinstance(policies, dict)
                else "research"
            )
            # register_release updates manifest_path/hash on conflict, so historical
            # registry rows remain replayable after the physical move to archive/.
            registry.register_release(release, governance_level=governance)
    return len(archived)


def _with_archive_count(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    release_id = str(payload.get("dataReleaseId") or "").strip() or None
    archived = _archive_standalone_history(settings, release_id)
    if archived:
        payload["archivedReleaseCount"] = archived
    return payload


def _recover_selected_dataset_alias(settings: Settings, release_id: str) -> dict[str, Any] | None:
    """Recover a missing DatasetVersion alias for the selected immutable release.

    Integrated mode keeps the historical explicit-selection guard. Standalone mode is
    intentionally self-healing: resolve_source() has already selected the newest active
    release, so a unique matching published DatasetVersion can restore both aliases
    without asking the user to copy/paste a content hash.
    """

    from qlib_platform.datasets.dataset_registry import DatasetRegistry

    registry = DatasetRegistry(settings.registry_path)
    if (
        settings.mode != "standalone"
        and registry.resolve_release_alias("research-release-current") != release_id
    ):
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
    return _with_archive_count(
        settings,
        {
            "status": "READY",
            "source": "dataset_version",
            "reference": settings.qlib_dataset_ref,
            "dataReleaseId": release_id,
            "datasetVersionId": dataset.version_id,
            "aliasRecovered": True,
        },
    )


def _release_bound_settings(settings: Settings, release_id: str) -> Settings:
    """Bind an immutable local DataRelease without requiring a user config overlay."""

    data = deepcopy(settings.data)
    data["data_source"] = {
        "kind": "data_release",
        "data_release": {
            "data_root": str(release_store_root(settings)),
            "id": release_id,
        },
    }
    return replace(settings, data=data)


def _materialize_selected_release(settings: Settings, release_id: str) -> dict[str, Any]:
    """Create the configured DatasetVersion directly from a frozen DataRelease."""

    from qlib_platform.datasets.data_release import materialize_data_release
    from qlib_platform.datasets.dataset_registry import DatasetRegistry
    from qlib_platform.datasets.qlib_export import dump_full

    store = FileReleaseStore(release_store_root(settings))
    release = store.resolve(
        release_id,
        mode="sampled",
        sample_size=64,
        workers=4,
    )

    # Imported Qlib releases already contain the provider bytes. Re-importing that
    # component is lossless and avoids routing it through the Parquet staging adapter.
    if "qlib_dataset" in release.components:
        source = release.manifest_path.parent / "components" / "qlib_dataset"
        normalized_release, dataset = import_qlib_dataset(settings, source)
        return _with_archive_count(
            settings,
            {
                "status": "READY",
                "source": "data_release",
                "reference": settings.qlib_dataset_ref,
                "dataReleaseId": normalized_release.data_release_id,
                "datasetVersionId": dataset.version_id,
                "materialized": True,
            },
        )

    bound = _release_bound_settings(settings, release_id)
    materialized = materialize_data_release(bound)
    path = dump_full(
        bound,
        sync_context={
            "data_release_id": materialized.data_release_id,
            "data_release_manifest_sha256": materialized.manifest_sha256,
            "dataset_parents": [
                {"version_id": materialized.data_release_id, "relation": "converted_from"}
            ],
        },
        promote_alias=False,
    )
    manifest_path = path / "dataset_manifest.json"
    dataset_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    registry = DatasetRegistry(settings.registry_path)
    policies = release.manifest.get("policies", {})
    governance = (
        str(policies.get("governanceLevel") or "research")
        if isinstance(policies, dict)
        else "research"
    )
    registry.register_release(release, governance_level=governance)
    registry.promote_research_snapshot(
        release_alias="research-release-current",
        data_release_id=release.data_release_id,
        dataset_alias=settings.qlib_dataset_ref,
        dataset_version_id=str(dataset_payload["version_id"]),
    )
    return _with_archive_count(
        settings,
        {
            "status": "READY",
            "source": "data_release",
            "reference": settings.qlib_dataset_ref,
            "dataReleaseId": release.data_release_id,
            "datasetVersionId": str(dataset_payload["version_id"]),
            "materialized": True,
        },
    )


def _ready_from_resolution(settings: Settings, resolved) -> dict[str, Any]:
    release_id = str(resolved.reference or "").strip() if resolved.source == "data_release" else ""
    return _with_archive_count(
        settings,
        {
            "status": resolved.status,
            "source": resolved.source,
            "reference": resolved.reference,
            "action": resolved.action,
            "profile": resolved.profile,
            "missingComponents": list(resolved.missing_components),
            **({"dataReleaseId": release_id} if release_id else {}),
        },
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
        try:
            resolved = resolve_source(settings)
        except ReleaseSelectionRequired as exc:
            # This remains a deliberate integrated-mode escape hatch. Standalone mode
            # resolves the newest active release automatically and never asks for ds_*.
            return {
                "status": exc.code,
                "error": str(exc),
                "recommendedCommand": "tq release list",
                "selectionCommand": (
                    "tq release promote <DATA_RELEASE_ID> --alias research-release-current"
                ),
                "datasetRecoveryCommand": f"tq registry-rebuild --root {settings.paths.root}",
                "retryCommand": "tq-research prepare --source auto",
            }
        if resolved.status == "MATERIALIZE_REQUIRED" and resolved.reference:
            recovered = _recover_selected_dataset_alias(settings, resolved.reference)
            if recovered is not None:
                return recovered
            return _materialize_selected_release(settings, resolved.reference)
        if resolved.status == "IMPORT_REQUIRED":
            release, dataset = import_qlib_dataset(settings, resolved.path or settings.qlib_data_uri)
            return _with_archive_count(
                settings,
                {
                    "status": "READY",
                    "source": "qlib",
                    "dataReleaseId": release.data_release_id,
                    "datasetVersionId": dataset.version_id,
                },
            )
        if resolved.status == "BUILD_REQUIRED":
            selected_start = start or str(settings.data.get("start_date") or "")
            selected_end = end or str(settings.data.get("end_date") or "")
            if resolved.profile == "ashare_market_import_v1":
                release, dataset = publish_local_market_release(
                    settings,
                    start=selected_start,
                    end=selected_end,
                )
                return _with_archive_count(
                    settings,
                    {
                        "status": "READY",
                        "source": "local_raw",
                        "profile": release.profile,
                        "governanceLevel": "exploratory",
                        "dataReleaseId": release.data_release_id,
                        "datasetVersionId": dataset.version_id,
                        "missingCertifiedComponents": list(resolved.missing_components),
                    },
                )
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
        return _ready_from_resolution(settings, resolved)
    if source == "qlib":
        if path is None:
            path = settings.qlib_data_uri
        release, dataset = import_qlib_dataset(settings, path)
        return _with_archive_count(
            settings,
            {
                "status": "READY",
                "source": "qlib",
                "dataReleaseId": release.data_release_id,
                "datasetVersionId": dataset.version_id,
            },
        )
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
            return _with_archive_count(
                settings,
                {
                    "status": "READY",
                    "source": "local_raw",
                    "profile": release.profile,
                    "governanceLevel": "exploratory",
                    "dataReleaseId": release.data_release_id,
                    "datasetVersionId": dataset.version_id,
                    "missingCertifiedComponents": list(resolved.missing_components),
                },
            )
        _run_cli(
            settings,
            "dataset-build",
            "--start",
            selected_start,
            "--end",
            selected_end,
        )
        after = resolve_source(settings)
        return _ready_from_resolution(settings, after)
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
        after = resolve_source(settings)
        result = _ready_from_resolution(settings, after)
        result["source"] = "tushare"
        return result
    raise ValueError(f"unsupported bootstrap source: {source}")
