from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import pyarrow.parquet as pq

from .settings import Settings


SCHEMA_VERSION = "2.0"
REQUIRED_RESEARCH_COMPONENTS = frozenset(
    {
        "bars",
        "daily_basic",
        "adjustment_factors",
        "corporate_actions",
        "trade_status",
        "limit_prices",
        "st_status",
        "security_master",
        "trading_calendar",
        "pit_universe",
        "pit_fundamentals",
        "benchmark",
    }
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(root: Path, raw: str | Path, *, owner: str, base: Path | None = None) -> Path:
    unresolved = Path(raw).expanduser()
    unresolved = unresolved if unresolved.is_absolute() else (base or root) / unresolved
    if unresolved.is_symlink():
        raise ValueError(f"{owner} must not be a symlink")
    resolved = unresolved.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{owner} escapes the configured data root") from exc
    if not resolved.is_file():
        raise FileNotFoundError(f"{owner} is missing: {resolved}")
    return resolved


@dataclass(frozen=True)
class PlatformRelease:
    data_root: Path
    manifest_path: Path
    manifest: dict[str, Any]
    components: dict[str, dict[str, Any]]

    @property
    def data_release_id(self) -> str:
        return str(self.manifest["dataReleaseId"])

    @property
    def manifest_sha256(self) -> str:
        return str(self.manifest["manifestSha256"])

    @property
    def coverage(self) -> Mapping[str, Any]:
        value = self.manifest.get("coverage")
        return value if isinstance(value, Mapping) else {}

    def files(self, role: str) -> list[Path]:
        component = self.components.get(role)
        if component is None:
            raise ValueError(f"DataRelease component is missing: {role}")
        return [
            _inside(
                self.data_root,
                str(item["path"]),
                owner=f"DataRelease {role} file",
                base=self.manifest_path.parent,
            )
            for item in component["files"]
        ]


def load_platform_release(settings: Settings) -> PlatformRelease:
    config = settings.platform_release_config
    data_root = settings.platform_data_root
    manifest_path = settings.platform_release_manifest
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("DataRelease manifest must be a JSON object")
    if str(manifest.get("schemaVersion") or "") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported DataRelease schemaVersion: {manifest.get('schemaVersion')}")

    recorded_manifest_sha = str(manifest.get("manifestSha256") or "").lower()
    manifest_identity = {key: value for key, value in manifest.items() if key != "manifestSha256"}
    actual_manifest_sha = hashlib.sha256(_canonical_bytes(manifest_identity)).hexdigest()
    if recorded_manifest_sha != actual_manifest_sha:
        raise ValueError("DataRelease manifestSha256 does not match canonical manifest content")

    identity = {
        key: value
        for key, value in manifest.items()
        if key not in {"dataReleaseId", "identitySha256", "manifestSha256", "publishedAt"}
    }
    actual_identity_sha = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    release_id = str(manifest.get("dataReleaseId") or "")
    if str(manifest.get("identitySha256") or "") != actual_identity_sha:
        raise ValueError("DataRelease identitySha256 does not match its content identity")
    if release_id != f"ds_{actual_identity_sha}":
        raise ValueError("DataRelease ID does not match its content identity")
    configured_id = str(config.get("id") or "").strip()
    if configured_id and configured_id != release_id:
        raise ValueError("Configured DataRelease ID does not match the manifest")

    declared_required = set(manifest.get("requiredComponents") or [])
    if declared_required != REQUIRED_RESEARCH_COMPONENTS:
        raise ValueError("DataRelease requiredComponents does not match the research profile")

    raw_components = manifest.get("components")
    if not isinstance(raw_components, list):
        raise ValueError("DataRelease components must be a list")
    components: dict[str, dict[str, Any]] = {}
    for raw_component in raw_components:
        if not isinstance(raw_component, Mapping):
            raise ValueError("DataRelease component must be an object")
        component = dict(raw_component)
        role = str(component.get("role") or "")
        if not role or role in components:
            raise ValueError(f"Invalid or duplicate DataRelease component role: {role}")
        raw_files = component.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise ValueError(f"DataRelease component has no files: {role}")
        for item in raw_files:
            if not isinstance(item, Mapping):
                raise ValueError(f"Invalid DataRelease file entry: {role}")
            path = _inside(
                data_root,
                str(item.get("path") or ""),
                owner=f"DataRelease {role} file",
                base=manifest_path.parent,
            )
            expected = str(item.get("sha256") or "").lower()
            if len(expected) != 64 or _sha256_file(path) != expected:
                raise ValueError(f"DataRelease file checksum mismatch: {item.get('path')}")
            if int(item.get("sizeBytes") or 0) != path.stat().st_size:
                raise ValueError(f"DataRelease file size mismatch: {item.get('path')}")
        component_identity = {
            key: component.get(key)
            for key in (
                "role",
                "componentReleaseId",
                "datasetKey",
                "schemaVersion",
                "coverage",
                "files",
            )
        }
        if hashlib.sha256(_canonical_bytes(component_identity)).hexdigest() != component.get(
            "componentSha256"
        ):
            raise ValueError(f"DataRelease component checksum mismatch: {role}")
        components[role] = component
    missing = sorted(REQUIRED_RESEARCH_COMPONENTS - set(components))
    if missing:
        raise ValueError(f"DataRelease is missing required research components: {missing}")
    return PlatformRelease(data_root, manifest_path, manifest, components)


def platform_release_preflight(settings: Settings, start: str, end: str) -> dict[str, Any]:
    release = load_platform_release(settings)
    normalized_start, normalized_end = start.replace("-", ""), end.replace("-", "")
    coverage_start = str(release.coverage.get("start") or "").replace("-", "")
    coverage_end = str(release.coverage.get("end") or "").replace("-", "")
    failures: list[str] = []
    if not coverage_start or normalized_start < coverage_start:
        failures.append(f"coverage_start:{coverage_start or 'missing'}")
    if not coverage_end or normalized_end > coverage_end:
        failures.append(f"coverage_end:{coverage_end or 'missing'}")
    staging_role = settings.platform_qlib_staging_role
    if staging_role not in release.components:
        failures.append(f"missing_component:{staging_role}")
    return {
        "passed": not failures,
        "source": "platform_release",
        "data_release_id": release.data_release_id,
        "manifest_sha256": release.manifest_sha256,
        "coverage": dict(release.coverage),
        "failures": failures,
    }


def _replace_directory(candidate: Path, target: Path) -> None:
    backup = target.with_name(f".{target.name}.old.{uuid.uuid4().hex[:8]}")
    if target.exists():
        os.replace(target, backup)
    try:
        os.replace(candidate, target)
    except Exception:
        if backup.exists() and not target.exists():
            os.replace(backup, target)
        raise
    finally:
        shutil.rmtree(backup, ignore_errors=True)


def materialize_platform_release(settings: Settings) -> PlatformRelease:
    release = load_platform_release(settings)
    role = settings.platform_qlib_staging_role
    component = release.components.get(role)
    if component is None:
        raise ValueError(
            f"DataRelease must include the explicit {role!r} component; canonical table schemas are never guessed"
        )
    stage = settings.paths.staging_full
    stage.parent.mkdir(parents=True, exist_ok=True)
    candidate = stage.parent / f".{stage.name}.platform.{uuid.uuid4().hex[:12]}"
    candidate.mkdir(parents=True)
    files: dict[str, str] = {}
    try:
        for index, item in enumerate(component["files"]):
            source = _inside(
                release.data_root,
                str(item["path"]),
                owner=f"DataRelease {role} file",
                base=release.manifest_path.parent,
            )
            if source.suffix.lower() != ".parquet":
                raise ValueError(f"{role} accepts only Parquet files: {source.name}")
            schema_names = set(pq.read_schema(source).names)
            if not {"date", "symbol"}.issubset(schema_names):
                raise ValueError(f"{role} file must contain date and symbol columns: {source.name}")
            target = candidate / f"{index:05d}.parquet"
            shutil.copy2(source, target)
            digest = _sha256_file(target)
            if digest != str(item["sha256"]):
                raise ValueError(f"Materialized staging checksum mismatch: {source.name}")
            files[target.name] = digest
        (candidate / "staging_manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": "2.0",
                    "mode": "full",
                    "source": "platform_release",
                    "data_release_id": release.data_release_id,
                    "manifest_sha256": release.manifest_sha256,
                    "files": files,
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ),
            encoding="utf-8",
        )
        _replace_directory(candidate, stage)
    except Exception:
        shutil.rmtree(candidate, ignore_errors=True)
        raise

    universe_files = release.files("pit_universe")
    intervals = pd.concat((pd.read_parquet(path) for path in universe_files), ignore_index=True)
    required = {"instrument", "effective_from", "effective_to"}
    missing = required - set(intervals.columns)
    if missing:
        raise ValueError(f"pit_universe component is not a Qlib PIT interval view: {sorted(missing)}")
    configured = settings.data.get("universe", {})
    configured_path = configured.get("membership_file") if isinstance(configured, Mapping) else None
    universe_name = str(configured.get("instruments", "all")) if isinstance(configured, Mapping) else "all"
    membership = (
        Path(str(configured_path)).expanduser()
        if configured_path
        else settings.paths.metadata / "universe_membership" / f"{universe_name.lower()}.parquet"
    )
    if not membership.is_absolute():
        membership = (settings.config_path.parent / membership).resolve()
    membership.parent.mkdir(parents=True, exist_ok=True)
    temporary = membership.with_suffix(".parquet.tmp")
    intervals.to_parquet(temporary, index=False)
    os.replace(temporary, membership)
    return release
