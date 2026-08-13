from __future__ import annotations

import json
from dataclasses import replace
from dataclasses import dataclass
from pathlib import Path

from .dataset_registry import DatasetRegistry
from .settings import Settings
from .store import sha256_file


@dataclass(frozen=True)
class ResolvedDataset:
    reference: str
    version_id: str
    dataset_name: str
    data_path: Path
    manifest_path: Path
    manifest_sha256: str


def resolve_dataset(
    settings: Settings, reference: str | None = None, *, allow_legacy: bool = True
) -> ResolvedDataset:
    selected = reference or settings.qlib_dataset_ref
    registry = DatasetRegistry(settings.registry_path)
    if settings.registry_path.is_file():
        try:
            version = registry.resolve(selected, settings.qlib_dataset_name)
        except KeyError:
            if not allow_legacy:
                raise
        else:
            if not version.data_path.is_dir() or not version.manifest_path.is_file():
                raise FileNotFoundError(f"registered Qlib dataset is incomplete: {version.data_path}")
            return ResolvedDataset(
                selected,
                version.version_id,
                version.dataset_name,
                version.data_path,
                version.manifest_path,
                sha256_file(version.manifest_path),
            )
    if not allow_legacy:
        raise KeyError(f"unknown dataset reference: {selected}")
    path = settings.qlib_data_uri
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
    manifest_path = settings.qlib_data_uri / "dataset_manifest.json"
    if reference is None and manifest_path.is_file():
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        version_id = str(payload.get("version_id") or "")
        configured_raw = str(settings.data.get("qlib", {}).get("dataset_dir", "")).strip()
        configured_path: Path | None = None
        if configured_raw:
            configured_path = Path(configured_raw).expanduser()
            if not configured_path.is_absolute():
                configured_path = (settings.config_path.parent.parent / configured_path).resolve()
            else:
                configured_path = configured_path.resolve()
        explicitly_replaced = configured_path is not None and configured_path != settings.qlib_data_uri
        if (payload.get("schema_version") == "3.0" and version_id) or explicitly_replaced:
            resolved = ResolvedDataset(
                reference=version_id or "explicit-path",
                version_id=version_id or str(payload.get("sha256") or "unversioned"),
                dataset_name=str(payload.get("dataset_name") or settings.qlib_dataset_name),
                data_path=settings.qlib_data_uri,
                manifest_path=manifest_path,
                manifest_sha256=sha256_file(manifest_path),
            )
            return settings, resolved
    resolved = resolve_dataset(settings, reference, allow_legacy=allow_legacy)
    if settings.qlib_data_uri == resolved.data_path:
        return settings, resolved
    return replace(settings, qlib_data_uri=resolved.data_path), resolved
