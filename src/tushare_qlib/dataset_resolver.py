from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from .dataset_registry import DatasetRegistry
from .settings import Settings
from .store import sha256_file


class DataUnavailableError(FileNotFoundError):
    code = "DATA_UNAVAILABLE"


@dataclass(frozen=True)
class ResolvedDataset:
    reference: str
    version_id: str
    dataset_name: str
    data_path: Path
    manifest_path: Path
    manifest_sha256: str


_SHARED_RESEARCH_ALIAS = "research-current"
_DATASET_MANIFEST_SCHEMA = "3.0"
_USABLE_MANIFEST_STATES = {"VALIDATED", "PUBLISHED"}


def dataset_reference_candidates(settings: Settings, reference: str | None = None) -> tuple[str, ...]:
    """Return safe registry references for a DatasetVersion read.

    ``standalone-current`` remains the standalone publication alias.  When that alias has
    not been created yet, a standalone checkout may still read the canonical
    ``research-current`` alias from a shared/symlinked data root.  The fallback is
    deliberately read-only and only applies to the configured standalone default; it
    never rewrites either alias.
    """

    selected = reference or settings.qlib_dataset_ref
    candidates = [selected]
    if (
        selected == settings.qlib_dataset_ref
        and settings.mode == "standalone"
        and settings.qlib_dataset_ref == "standalone-current"
        and selected != _SHARED_RESEARCH_ALIAS
    ):
        candidates.append(_SHARED_RESEARCH_ALIAS)
    return tuple(candidates)


def current_manifest_dataset(settings: Settings, reference: str | None = None) -> ResolvedDataset | None:
    """Resolve the configured current provider from its immutable v3 manifest.

    A shared data root may contain a fully materialized ``qlib/current`` dataset even
    when registry aliases were never created (or were removed during migration).  The
    current manifest is a stronger selector than an unordered collection of historical
    DataReleases, so it is safe to use for the configured default reference only.
    Explicit unknown references must continue to fail closed.
    """

    selected = reference or settings.qlib_dataset_ref
    if selected != settings.qlib_dataset_ref:
        return None
    manifest = settings.qlib_data_uri / "dataset_manifest.json"
    if not manifest.is_file():
        return None
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    version_id = str(payload.get("version_id") or "").strip()
    status = str(payload.get("status") or "VALIDATED").upper()
    if (
        payload.get("schema_version") != _DATASET_MANIFEST_SCHEMA
        or not version_id
        or status not in _USABLE_MANIFEST_STATES
    ):
        return None
    data_path = Path(str(payload.get("data_path") or settings.qlib_data_uri)).expanduser().resolve()
    required = (data_path / "calendars" / "day.txt", data_path / "instruments", data_path / "features")
    if not data_path.is_dir() or any(not item.exists() for item in required):
        return None
    return ResolvedDataset(
        selected,
        version_id,
        str(payload.get("dataset_name") or payload.get("dataset_id") or settings.qlib_dataset_name),
        data_path,
        manifest,
        sha256_file(manifest),
    )


def resolve_dataset(
    settings: Settings, reference: str | None = None, *, allow_legacy: bool = True
) -> ResolvedDataset:
    selected = reference or settings.qlib_dataset_ref
    registry = DatasetRegistry(settings.registry_path)
    if settings.registry_path.is_file():
        for candidate in dataset_reference_candidates(settings, reference):
            # The configured alias is namespaced to the configured dataset name.  An
            # explicit reference or the canonical shared-data fallback is an immutable
            # identity override and may legitimately come from another profile name.
            expected_name = settings.qlib_dataset_name if candidate == settings.qlib_dataset_ref else None
            try:
                version = registry.resolve(candidate, expected_name)
            except KeyError:
                continue
            if not version.data_path.is_dir() or not version.manifest_path.is_file():
                raise FileNotFoundError(f"registered Qlib dataset is incomplete: {version.data_path}")
            return ResolvedDataset(
                candidate,
                version.version_id,
                version.dataset_name,
                version.data_path,
                version.manifest_path,
                sha256_file(version.manifest_path),
            )
    current = current_manifest_dataset(settings, reference)
    if current is not None:
        return current
    if not allow_legacy:
        raise KeyError(f"unknown dataset reference: {selected}")
    path = settings.qlib_data_uri
    required = (path / "calendars" / "day.txt", path / "instruments", path / "features")
    strict_local = settings.source_kind in {"local", "qlib", "dataset", "auto"}
    if strict_local and (not path.is_dir() or any(not item.exists() for item in required)):
        raise DataUnavailableError(f"DATA_UNAVAILABLE: no usable Qlib dataset at {path}")
    manifest = path / "dataset_manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {}
    return ResolvedDataset(
        "legacy",
        str(payload.get("version_id") or payload.get("sha256") or "unversioned"),
        str(payload.get("dataset_name") or payload.get("dataset_id") or path.name),
        path,
        manifest,
        sha256_file(manifest) if manifest.is_file() else "",
    )


def pin_dataset(
    settings: Settings, reference: str | None = None, *, allow_legacy: bool = True
) -> tuple[Settings, ResolvedDataset]:
    current = current_manifest_dataset(settings, reference)
    if current is not None:
        if settings.qlib_data_uri == current.data_path:
            return settings, current
        return replace(settings, qlib_data_uri=current.data_path), current
    resolved = resolve_dataset(settings, reference, allow_legacy=allow_legacy)
    if settings.qlib_data_uri == resolved.data_path:
        return settings, resolved
    return replace(settings, qlib_data_uri=resolved.data_path), resolved
