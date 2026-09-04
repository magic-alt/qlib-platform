from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from qlib_platform.datasets.dataset_manifest import verify_dataset_manifest
from qlib_platform.datasets.dataset_registry import DatasetRegistry, DatasetVersion
from qlib_platform.datasets.dataset_resolver import current_manifest_dataset, dataset_reference_candidates
from qlib_platform.releases import FileReleaseStore, release_store_root
from qlib_platform.releases import missing_market_components
from qlib_platform.settings import Settings


_SOURCE_PROBE_SAMPLE_SIZE = 64
_SOURCE_PROBE_WORKERS = 4


@dataclass(frozen=True)
class SourceResolution:
    status: str
    source: str | None
    reference: str | None = None
    path: Path | None = None
    action: str | None = None
    profile: str | None = None
    missing_components: tuple[str, ...] = ()


class ReleaseSelectionRequired(ValueError):
    code = "RELEASE_SELECTION_REQUIRED"


def _provider_ready(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / "calendars" / "day.txt").is_file()
        and (path / "instruments").is_dir()
        and (path / "features").is_dir()
    )


def _verify_dataset_source_probe(manifest_path: Path) -> None:
    """Bound source discovery I/O without relabeling a sampled check as deep verification."""

    verify_dataset_manifest(
        manifest_path,
        mode="sampled",
        sample_size=_SOURCE_PROBE_SAMPLE_SIZE,
        workers=_SOURCE_PROBE_WORKERS,
    )


def _missing_certified_raw(settings: Settings) -> tuple[str, ...]:
    universe_cfg = settings.data.get("universe", {})
    instruments = (
        str(universe_cfg.get("instruments") or "all").lower() if isinstance(universe_cfg, dict) else "all"
    )
    required = {
        "daily_basic": settings.paths.raw / "daily_basic",
        "corporate_actions": settings.paths.raw / "dividend",
        "trade_status": settings.paths.raw / "suspend_d",
        "limit_prices": settings.paths.raw / "stk_limit",
        "st_status": settings.paths.raw / "stock_st",
        "pit_universe": settings.paths.metadata / "universe_membership" / f"{instruments}.parquet",
        "pit_fundamentals_source": settings.paths.raw / "extended" / "fina_indicator_vip",
        "benchmark": settings.paths.metadata / "benchmarks" / "SH000300.parquet",
        "industry_classification_pit": settings.paths.metadata / "industry_classification_pit.parquet",
    }
    missing: list[str] = []
    for role, path in required.items():
        ready = path.is_file() if path.suffix else path.is_dir() and any(path.rglob("*.parquet"))
        if not ready:
            missing.append(role)
    return tuple(missing)


def _registered_dataset(
    settings: Settings, registry: DatasetRegistry
) -> tuple[str | None, DatasetVersion | None]:
    """Find the configured dataset or the safe shared-data read fallback."""

    for reference in dataset_reference_candidates(settings):
        dataset = registry.inspect(reference)
        if dataset is None:
            continue
        if reference == settings.qlib_dataset_ref and dataset.dataset_name != settings.qlib_dataset_name:
            raise ValueError(
                f"dataset reference {reference!r} resolves to {dataset.dataset_name!r}, "
                f"expected {settings.qlib_dataset_name!r}"
            )
        if dataset.status != "PUBLISHED":
            raise ValueError(f"dataset reference is not published: {dataset.version_id}")
        return reference, dataset
    return None, None


def resolve_local_raw_source(settings: Settings) -> SourceResolution:
    if not (settings.paths.raw / "daily").is_dir():
        return SourceResolution(
            "DATA_INCOMPLETE",
            "local_raw",
            path=settings.paths.raw,
            action="add required local market components",
            profile="ashare_market_import_v1",
            missing_components=("bars",),
        )
    market_missing = tuple(missing_market_components(settings))
    certified_missing = _missing_certified_raw(settings)
    if not certified_missing and not market_missing:
        return SourceResolution(
            "BUILD_REQUIRED",
            "local_raw",
            path=settings.paths.raw,
            action="release build-local",
            profile="ashare_qlib_research_v2",
        )
    if not market_missing:
        return SourceResolution(
            "BUILD_REQUIRED",
            "local_raw",
            path=settings.paths.raw,
            action="bootstrap --source raw",
            profile="ashare_market_import_v1",
            missing_components=certified_missing,
        )
    return SourceResolution(
        "DATA_INCOMPLETE",
        "local_raw",
        path=settings.paths.raw,
        action="add required local market components",
        profile="ashare_market_import_v1",
        missing_components=market_missing,
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
    dataset_reference, dataset = _registered_dataset(settings, registry)
    release_alias = registry.resolve_release_alias("research-release-current")
    if release_alias:
        release = store.resolve(
            release_alias,
            mode="sampled",
            sample_size=_SOURCE_PROBE_SAMPLE_SIZE,
            workers=_SOURCE_PROBE_WORKERS,
        )
        if dataset is not None and dataset.data_release_id == release.data_release_id:
            _verify_dataset_source_probe(dataset.manifest_path)
            return SourceResolution("READY", "data_release", release.data_release_id, dataset.data_path)
        return SourceResolution(
            "MATERIALIZE_REQUIRED",
            "data_release",
            release.data_release_id,
            release.manifest_path,
            "dataset-build",
        )
    if dataset is not None:
        _verify_dataset_source_probe(dataset.manifest_path)
        return SourceResolution("READY", "dataset_version", dataset_reference, dataset.data_path)
    current = current_manifest_dataset(settings)
    if current is not None:
        _verify_dataset_source_probe(current.manifest_path)
        return SourceResolution("READY", "dataset_current", current.reference, current.data_path)
    if _provider_ready(settings.qlib_data_uri):
        # The configured current provider is an explicit local selector even when it
        # predates DatasetVersion manifests. Normalize it into an immutable
        # DataRelease/DatasetVersion before considering unordered historical releases.
        # This is fail-closed with respect to history: we import the configured bytes;
        # we never guess which historical release should become active.
        return SourceResolution(
            "IMPORT_REQUIRED",
            "qlib",
            "legacy",
            settings.qlib_data_uri,
            "release import-qlib",
        )
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
    if (settings.paths.raw / "daily").is_dir():
        return resolve_local_raw_source(settings)
    if settings.tushare_token:
        return SourceResolution(
            "DOWNLOAD_REQUIRED",
            "tushare",
            action="bootstrap --source tushare --start YYYYMMDD --end YYYYMMDD",
        )
    return SourceResolution("DATA_UNAVAILABLE", None)
