from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Mapping

import pandas as pd
import pyarrow.parquet as pq
import fastjsonschema

from .settings import Settings
from .runtime_resources import resource_path
from .verification import (
    deterministic_sample,
    load_verification_receipt,
    normalize_verification_mode,
    write_verification_receipt,
)


SCHEMA_VERSION = "2.0"
CORE_RESEARCH_COMPONENTS = frozenset(
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
REQUIRED_RESEARCH_COMPONENTS = CORE_RESEARCH_COMPONENTS
QLIB_RESEARCH_PROFILE = "ashare_qlib_research_v1"
QLIB_RESEARCH_PROFILE_V2 = "ashare_qlib_research_v2"
QLIB_IMPORT_PROFILE = "ashare_qlib_import_v1"
MARKET_IMPORT_PROFILE = "ashare_market_import_v1"
DATA_RELEASE_PROFILES = {
    "cn-equity-daily-research-v2": CORE_RESEARCH_COMPONENTS,
    QLIB_RESEARCH_PROFILE: CORE_RESEARCH_COMPONENTS | {"qlib_staging", "industry_classification_pit"},
    QLIB_RESEARCH_PROFILE_V2: CORE_RESEARCH_COMPONENTS | {"qlib_staging", "industry_classification_pit"},
    QLIB_IMPORT_PROFILE: frozenset({"qlib_dataset"}),
    MARKET_IMPORT_PROFILE: frozenset({"bars", "adjustment_factors", "security_master", "trading_calendar"}),
}
PROFILE_COMPONENT_SCHEMAS = {
    QLIB_RESEARCH_PROFILE_V2: {
        "pit_fundamentals": "2",
        "industry_classification_pit": "1",
        "qlib_staging": "qlib-staging-v2",
    },
    QLIB_IMPORT_PROFILE: {"qlib_dataset": "qlib-provider-v1"},
    MARKET_IMPORT_PROFILE: {
        "bars": "1",
        "adjustment_factors": "1",
        "security_master": "1",
        "trading_calendar": "1",
    },
}


def _validate_contract_schema(manifest: Mapping[str, Any]) -> None:
    schema_path = resource_path("contracts/data-release.v2.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    try:
        fastjsonschema.validate(schema, manifest)
    except fastjsonschema.JsonSchemaException as exc:
        raise ValueError(f"DataRelease v2 schema validation failed: {exc.message}") from exc


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _integer(value: object) -> int:
    return int(value) if isinstance(value, (str, int, float)) else 0


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(root: Path, raw: str | Path, *, owner: str, base: Path | None = None) -> Path:
    unresolved = Path(raw).expanduser()
    unresolved = unresolved if unresolved.is_absolute() else (base or root) / unresolved
    cursor = unresolved
    while True:
        if cursor.is_symlink():
            raise ValueError(f"{owner} must not contain symlinks")
        if cursor == root or cursor.parent == cursor:
            break
        cursor = cursor.parent
    resolved = unresolved.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{owner} escapes the configured data root") from exc
    if not resolved.is_file():
        raise FileNotFoundError(f"{owner} is missing: {resolved}")
    return resolved


def _declared_file(root: Path, raw: object, *, owner: str, base: Path) -> Path:
    value = str(raw or "")
    declared = Path(value)
    if not value or ".." in PurePosixPath(value.replace("\\", "/")).parts:
        raise ValueError(f"{owner} has an invalid path")
    target = declared if declared.is_absolute() else base / declared
    try:
        target.absolute().relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{owner} escapes the configured data root") from exc
    return target


@dataclass(frozen=True)
class DataRelease:
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
    def profile(self) -> str:
        return str(self.manifest["profile"])

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


def load_data_release(settings: Settings) -> DataRelease:
    config = settings.data_release_config
    return verify_data_release(
        settings.platform_data_root,
        settings.platform_release_manifest,
        configured_id=str(config.get("id") or config.get("ref") or "").strip() or None,
        mode="deep",
        workers=4,
    )


def verify_data_release(
    data_root: str | Path,
    manifest_path: str | Path,
    *,
    configured_id: str | None = None,
    mode: str = "deep",
    receipt_dir: str | Path | None = None,
    reuse_receipt: bool = False,
    sample_size: int = 64,
    evidence: dict[str, object] | None = None,
    verified_digests: set[str] | None = None,
    workers: int = 1,
) -> DataRelease:
    if workers < 1:
        raise ValueError("verification workers must be positive")
    normalized_mode = normalize_verification_mode(mode)
    data_root = Path(data_root).expanduser().resolve()
    raw_manifest = Path(manifest_path).expanduser()
    if raw_manifest.is_symlink():
        raise ValueError("DataRelease manifest must not be a symlink")
    manifest_path = raw_manifest.resolve()
    try:
        manifest_path.relative_to(data_root)
    except ValueError as exc:
        raise ValueError("DataRelease manifest must remain under the configured data root") from exc
    if not manifest_path.is_file():
        raise FileNotFoundError(f"DataRelease manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("DataRelease manifest must be a JSON object")
    _validate_contract_schema(manifest)
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
    if configured_id and configured_id != release_id:
        raise ValueError("Configured DataRelease ID does not match the manifest")

    profile = str(manifest.get("profile") or "")
    if profile not in DATA_RELEASE_PROFILES:
        raise ValueError(f"Unknown DataRelease profile: {profile}")
    expected_required = frozenset(DATA_RELEASE_PROFILES[profile])
    declared_required = frozenset(manifest.get("requiredComponents") or [])
    if declared_required != expected_required:
        raise ValueError("DataRelease requiredComponents does not match its profile")

    raw_components = manifest.get("components")
    if not isinstance(raw_components, list):
        raise ValueError("DataRelease components must be a list")
    components: dict[str, dict[str, Any]] = {}
    declared_files: list[Mapping[str, object]] = []
    seen_paths: set[str] = set()
    total_bytes = 0
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
            declared_path = str(item.get("path") or "")
            _declared_file(
                data_root,
                declared_path,
                owner=f"DataRelease {role} file",
                base=manifest_path.parent,
            )
            if declared_path in seen_paths:
                raise ValueError(f"Duplicate DataRelease file path: {declared_path}")
            seen_paths.add(declared_path)
            expected = str(item.get("sha256") or "").lower()
            if len(expected) != 64 or any(value not in "0123456789abcdef" for value in expected):
                raise ValueError(f"Invalid DataRelease file checksum: {declared_path}")
            size_bytes = int(item.get("sizeBytes") or 0)
            if size_bytes < 0:
                raise ValueError(f"Invalid DataRelease file size: {declared_path}")
            total_bytes += size_bytes
            declared_files.append(item)
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
    missing = sorted(expected_required - set(components))
    if missing:
        raise ValueError(f"DataRelease is missing required research components: {missing}")
    for role, expected_schema in PROFILE_COMPONENT_SCHEMAS.get(profile, {}).items():
        actual_schema = str(components[role].get("schemaVersion") or "")
        if actual_schema != expected_schema:
            raise ValueError(
                "DataRelease component schema mismatch: "
                f"{role} expected {expected_schema}, got {actual_schema or 'missing'}"
            )
    receipt = None
    if normalized_mode == "deep" and reuse_receipt and receipt_dir is not None:
        receipt = load_verification_receipt(
            receipt_dir,
            artifact_kind="data_release",
            artifact_id=release_id,
            manifest_sha256=recorded_manifest_sha,
        )
        if receipt is not None:
            _, receipt_payload = receipt
            if (
                _integer(receipt_payload.get("fileCount", -1)) != len(declared_files)
                or _integer(receipt_payload.get("totalBytes", -1)) != total_bytes
            ):
                raise ValueError("verification receipt file inventory mismatch")
    selected: list[Mapping[str, object]] = []
    if receipt is None and normalized_mode == "sampled":
        selected = deterministic_sample(
            declared_files,
            identity=release_id,
            path_key="path",
            sample_size=sample_size,
        )
    elif receipt is None and normalized_mode == "deep":
        selected = declared_files

    def verify_file(item: Mapping[str, object]) -> str | None:
        path = _inside(
            data_root,
            str(item.get("path") or ""),
            owner="DataRelease file",
            base=manifest_path.parent,
        )
        expected = str(item.get("sha256") or "").lower()
        if _integer(item.get("sizeBytes")) != path.stat().st_size:
            raise ValueError(f"DataRelease file checksum mismatch (size drift): {item.get('path')}")
        object_path = data_root / "objects" / expected[:2] / expected
        linked_object = object_path.is_file() and os.path.samefile(path, object_path)
        checksum_path = object_path if linked_object else path
        if _sha256_file(checksum_path) != expected:
            raise ValueError(f"DataRelease file checksum mismatch: {item.get('path')}")
        return expected if linked_object else None

    if workers == 1 or len(selected) < 2:
        verified_results = [verify_file(item) for item in selected]
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="release-verify") as executor:
            verified_results = list(executor.map(verify_file, selected))
    verified_objects = {digest for digest in verified_results if digest is not None}
    if verified_digests is not None:
        verified_digests.update(verified_objects)
    receipt_path: Path | None = receipt[0] if receipt is not None else None
    if normalized_mode == "deep" and receipt is None and receipt_dir is not None:
        receipt_path = write_verification_receipt(
            receipt_dir,
            artifact_kind="data_release",
            artifact_id=release_id,
            manifest_sha256=recorded_manifest_sha,
            file_count=len(declared_files),
            total_bytes=total_bytes,
        )
    if evidence is not None:
        evidence.update(
            {
                "mode": normalized_mode,
                "verificationSource": "receipt" if receipt is not None else "files",
                "manifestSha256": recorded_manifest_sha,
                "fileCount": len(declared_files),
                "verifiedFileCount": 0 if receipt is not None else len(selected),
                "verifiedUniqueObjects": len(verified_objects),
                "workers": workers,
                "totalBytes": total_bytes,
                "receipt": str(receipt_path) if receipt_path is not None else None,
            }
        )
    return DataRelease(data_root, manifest_path, manifest, components)


def data_release_preflight(settings: Settings, start: str, end: str) -> dict[str, Any]:
    release = load_data_release(settings)
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
        "source": "data_release",
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


def _attach_industry_component(release: DataRelease, candidate: Path) -> None:
    if "industry_classification_pit" not in release.components:
        return
    intervals = pd.concat(
        (pd.read_parquet(path) for path in release.files("industry_classification_pit")),
        ignore_index=True,
    )
    required = {
        "instrument",
        "effective_from",
        "effective_to",
        "industry_code",
        "taxonomy",
        "level_no",
    }
    missing = required - set(intervals.columns)
    if missing:
        raise ValueError(f"industry_classification_pit schema is incomplete: {sorted(missing)}")
    if (
        not intervals["taxonomy"].eq("SW2021").all()
        or not pd.to_numeric(intervals["level_no"], errors="raise").eq(1).all()
    ):
        raise ValueError("industry_classification_pit requires SW2021 level 1")
    intervals = intervals.copy()
    intervals["effective_from"] = pd.to_datetime(intervals["effective_from"], errors="raise").dt.normalize()
    intervals["effective_to"] = pd.to_datetime(intervals["effective_to"], errors="raise").dt.normalize()
    intervals["industry_l1_code"] = pd.to_numeric(intervals["industry_code"], errors="raise")
    intervals = intervals.sort_values(["instrument", "effective_from", "industry_l1_code"])
    for instrument, group in intervals.groupby("instrument", sort=False):
        if (group["effective_from"].iloc[1:].to_numpy() <= group["effective_to"].iloc[:-1].to_numpy()).any():
            raise ValueError(f"Overlapping PIT industry intervals: {instrument}")
    for path in sorted(candidate.glob("*.parquet")):
        frame = pd.read_parquet(path)
        dates = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
        codes = pd.Series(float("nan"), index=frame.index, dtype="float64")
        symbols = frame["symbol"].astype(str).str.upper()
        for row in intervals.itertuples(index=False):
            mask = (
                symbols.eq(str(row.instrument).upper())
                & dates.ge(row.effective_from)
                & dates.le(row.effective_to)
            )
            codes.loc[mask] = float(row.industry_l1_code)
        frame["industry_l1_code"] = codes
        temporary = path.with_suffix(".parquet.tmp")
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, path)


def materialize_data_release(settings: Settings) -> DataRelease:
    release = load_data_release(settings)
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
    coverage_start = pd.Timestamp(str(release.coverage["start"])).normalize()
    coverage_end = pd.Timestamp(str(release.coverage["end"])).normalize()
    try:
        materialized_count = 0
        for item in component["files"]:
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
            frame = pd.read_parquet(source)
            dates = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
            selected = frame.loc[dates.between(coverage_start, coverage_end)]
            if selected.empty:
                continue
            symbols = selected["symbol"].dropna().astype(str).str.strip().str.upper().unique()
            if len(symbols) != 1 or re.fullmatch(r"(?:SH|SZ|BJ)\d{6}", symbols[0]) is None:
                raise ValueError(f"{role} file must contain exactly one valid Qlib symbol: {source.name}")
            target = candidate / f"{symbols[0]}.parquet"
            if target.exists():
                raise ValueError(f"{role} contains duplicate source files for symbol: {symbols[0]}")
            selected.to_parquet(target, index=False)
            materialized_count += 1
        if materialized_count == 0:
            raise ValueError(f"{role} contains no rows inside the DataRelease coverage")
        _attach_industry_component(release, candidate)
        for materialized in sorted(candidate.glob("*.parquet")):
            files[materialized.name] = _sha256_file(materialized)
        (candidate / "staging_manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": "2.0",
                    "mode": "full",
                    "source": "data_release",
                    "data_release_id": release.data_release_id,
                    "manifest_sha256": release.manifest_sha256,
                    "coverage": {
                        "start": str(coverage_start.date()),
                        "end": str(coverage_end.date()),
                    },
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
