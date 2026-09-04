from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from qlib_platform.datasets.data_release import DataRelease
from qlib_platform.datasets.qlib_staging_contract import (
    QlibStagingContractError,
    validate_qlib_staging_files,
)
from qlib_platform.releases.publisher import (
    ComponentSource,
    LocalReleasePublisher,
    release_store_root,
)
from qlib_platform.settings import Settings


_TRANSIENT_STAGING_DIRS = frozenset({".curated_by_symbol", ".duckdb_spill"})


@dataclass(frozen=True)
class StagingInventory:
    canonical: tuple[Path, ...]
    transient: tuple[Path, ...]


@dataclass(frozen=True)
class StagingRepairResult:
    release: DataRelease
    ignored_transient_files: tuple[str, ...]


def inspect_qlib_staging_inventory(release: DataRelease) -> StagingInventory:
    """Separate flat qlib-staging-v2 inputs from known producer-owned scratch files.

    ``export_full_staging`` writes the durable Qlib inputs directly under
    ``qlib_staging``. DuckDB partition/spill directories are implementation details and
    are never part of the semantic qlib-staging-v2 payload. Historical publishers used
    recursive discovery, so an interrupted build could accidentally freeze those
    scratch parquet files into an otherwise valid immutable DataRelease.

    Unknown nested parquet remains fail-closed: only the two producer-owned transient
    namespaces are repairable without consulting the original raw source.
    """

    role = "qlib_staging"
    root = (release.manifest_path.parent / "components" / role).resolve()
    canonical: list[Path] = []
    transient: list[Path] = []
    for path in release.files(role):
        try:
            relative = path.resolve().relative_to(root)
        except ValueError as exc:
            raise QlibStagingContractError(f"{role} file escapes its component root: {path}") from exc
        if len(relative.parts) == 1:
            canonical.append(path)
            continue
        if relative.parts[0] in _TRANSIENT_STAGING_DIRS:
            transient.append(path)
            continue
        raise QlibStagingContractError(f"{role} contains unsupported nested parquet: {relative.as_posix()}")

    validate_qlib_staging_files(canonical, role=role)
    return StagingInventory(tuple(canonical), tuple(transient))


def _link_or_copy(source: Path, target: Path) -> None:
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def repair_transient_qlib_staging_release(
    settings: Settings,
    release: DataRelease,
) -> StagingRepairResult | None:
    """Derive a clean immutable release when only staging scratch files leaked in.

    The parent DataRelease is never edited. All non-staging components are replayed
    byte-for-byte from the verified parent release; qlib_staging is republished from
    the validated flat canonical files only. The new release records explicit parent
    lineage and can then flow through the normal DatasetVersion materializer.
    """

    if "qlib_staging" not in release.components:
        return None
    inventory = inspect_qlib_staging_inventory(release)
    if not inventory.transient:
        return None

    store_root = release_store_root(settings)
    store_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".repair-qlib-staging.", dir=store_root) as raw_temp:
        sanitized = Path(raw_temp) / "qlib_staging"
        sanitized.mkdir()
        for source in inventory.canonical:
            _link_or_copy(source, sanitized / source.name)

        sources: list[ComponentSource] = []
        for role, component in release.components.items():
            source_path = (
                sanitized if role == "qlib_staging" else release.manifest_path.parent / "components" / role
            )
            dataset_key = str(component.get("datasetKey") or "").strip() or None
            sources.append(
                ComponentSource(
                    role,
                    source_path,
                    schema_version=str(component.get("schemaVersion") or "1"),
                    dataset_key=dataset_key,
                )
            )

        lineage = _mapping(release.manifest.get("lineage"))
        lineage.update(
            {
                "parentReleaseId": release.data_release_id,
                "repairReason": "qlib_staging_transient_inventory",
                "ignoredTransientFileCount": len(inventory.transient),
            }
        )
        coverage = {
            "start": str(release.coverage.get("start") or ""),
            "end": str(release.coverage.get("end") or ""),
        }
        repaired = LocalReleasePublisher(store_root).publish(
            profile=release.profile,
            components=sources,
            coverage=coverage,
            asset_class=str(release.manifest.get("assetClass") or "equity"),
            market=str(release.manifest.get("market") or "china"),
            universe=str(release.manifest.get("universe") or "CSI300"),
            benchmark=str(release.manifest.get("benchmark") or "SH000300"),
            policies=_mapping(release.manifest.get("policies")),
            lineage=lineage,
            as_of_time=str(release.manifest.get("asOfTime") or "") or None,
        )

    root = release.manifest_path.parent / "components" / "qlib_staging"
    ignored = tuple(path.relative_to(root).as_posix() for path in inventory.transient)
    return StagingRepairResult(repaired, ignored)
