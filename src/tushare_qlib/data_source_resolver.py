from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .dataset_manifest import verify_dataset_manifest
from .dataset_registry import DatasetRegistry
from .releases import FileReleaseStore, release_store_root
from .settings import Settings


@dataclass(frozen=True)
class SourceResolution:
    status: str
    source: str | None
    reference: str | None = None
    path: Path | None = None
    action: str | None = None


class ReleaseSelectionRequired(ValueError):
    code = "RELEASE_SELECTION_REQUIRED"


def _provider_ready(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / "calendars" / "day.txt").is_file()
        and (path / "instruments").is_dir()
        and (path / "features").is_dir()
    )


def resolve_source(
    settings: Settings,
    *,
    explicit_release: str | None = None,
) -> SourceResolution:
    store = FileReleaseStore(release_store_root(settings))
    if explicit_release:
        release = store.resolve(explicit_release)
        return SourceResolution(
            "MATERIALIZE_REQUIRED",
            "data_release",
            release.data_release_id,
            release.manifest_path,
            "dataset-build",
        )
    registry = DatasetRegistry(settings.registry_path)
    release_alias = registry.resolve_release_alias("research-release-current")
    if release_alias:
        release = store.resolve(release_alias)
        dataset = registry.inspect(settings.qlib_dataset_ref)
        if dataset is not None and dataset.data_release_id == release.data_release_id:
            verify_dataset_manifest(dataset.manifest_path)
            return SourceResolution("READY", "data_release", release.data_release_id, dataset.data_path)
        return SourceResolution(
            "MATERIALIZE_REQUIRED",
            "data_release",
            release.data_release_id,
            release.manifest_path,
            "dataset-build",
        )
    dataset = registry.inspect(settings.qlib_dataset_ref)
    if dataset is not None:
        verify_dataset_manifest(dataset.manifest_path)
        return SourceResolution("READY", "dataset_version", dataset.version_id, dataset.data_path)
    records = list(store.list())
    if len(records) == 1:
        record = records[0]
        return SourceResolution(
            "MATERIALIZE_REQUIRED",
            "data_release",
            record.data_release_id,
            record.manifest_path,
            "release promote",
        )
    if len(records) > 1:
        raise ReleaseSelectionRequired(
            "RELEASE_SELECTION_REQUIRED: multiple DataReleases exist without an active alias"
        )
    if _provider_ready(settings.qlib_data_uri):
        return SourceResolution(
            "IMPORT_REQUIRED",
            "qlib",
            "legacy",
            settings.qlib_data_uri,
            "release import-qlib",
        )
    if (settings.paths.raw / "daily").is_dir():
        return SourceResolution(
            "BUILD_REQUIRED", "local_raw", path=settings.paths.raw, action="release build-local"
        )
    if settings.tushare_token:
        return SourceResolution(
            "DOWNLOAD_REQUIRED",
            "tushare",
            action="bootstrap --source tushare --start YYYYMMDD --end YYYYMMDD",
        )
    return SourceResolution("DATA_UNAVAILABLE", None)
