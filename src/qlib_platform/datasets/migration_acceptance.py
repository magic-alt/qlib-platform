from __future__ import annotations

import copy
import json
import os
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import pyarrow.parquet as pq

from qlib_platform.datasets.data_release import (
    QLIB_RESEARCH_PROFILE_V2,
    materialize_data_release,
    verify_data_release,
)
from qlib_platform.datasets.dataset_manifest import verify_dataset_manifest
from qlib_platform.datasets.dataset_registry import DatasetRegistry
from qlib_platform.lineage import git_revision, resolve_qlib_repo
from qlib_platform.datasets.qlib_export import dump_full
from qlib_platform.releases import LocalReleasePublisher, import_qlib_dataset
from qlib_platform.releases.publisher import local_research_components
from qlib_platform.settings import Paths, Settings
from qlib_platform.data.store import sha256_file


def _assert_isolated(source: Path, acceptance_root: Path) -> None:
    if not source.is_dir() or source.is_symlink():
        raise ValueError(f"migration source must be a regular directory: {source}")
    for parent, child in ((source, acceptance_root), (acceptance_root, source)):
        try:
            child.relative_to(parent)
        except ValueError:
            continue
        raise ValueError("migration source and acceptance root must not overlap")
    if acceptance_root.exists() and any(acceptance_root.iterdir()):
        raise ValueError("migration acceptance root must be absent or empty")


def _isolated_settings(base: Settings, root: Path) -> Settings:
    data = copy.deepcopy(base.data)
    paths = Paths.from_root(root)
    data["project_root"] = str(root)
    data["mode"] = "standalone"
    data["storage"] = {"registry_path": str(paths.registry / "qlib.sqlite")}
    data["release_store"] = {"kind": "file", "root": str(root / "releases")}
    qlib = data.setdefault("qlib", {})
    qlib["dataset_dir"] = str(root / "qlib" / "current")
    qlib["versions_root"] = str(paths.qlib_versions)
    universe = data.setdefault("universe", {})
    instruments = str(universe.get("instruments") or "all").lower()
    universe["membership_file"] = str(paths.metadata / "universe_membership" / f"{instruments}.parquet")
    return replace(
        base,
        data=data,
        paths=paths,
        qlib_data_uri=(root / "qlib" / "current").resolve(),
        tushare_token=None,
    )


def _source_settings(base: Settings, source_root: Path, acceptance_root: Path) -> Settings:
    data = copy.deepcopy(base.data)
    paths = Paths.from_root(source_root)
    data["project_root"] = str(source_root)
    data["release_store"] = {"kind": "file", "root": str(acceptance_root / "releases")}
    universe = data.setdefault("universe", {})
    instruments = str(universe.get("instruments") or "all").lower()
    universe["membership_file"] = str(paths.metadata / "universe_membership" / f"{instruments}.parquet")
    return replace(base, data=data, paths=paths, qlib_data_uri=(source_root / "qlib" / "current"))


def _write_evidence(root: Path, payload: Mapping[str, object]) -> Path:
    target = root / "acceptance_evidence.json"
    descriptor, temporary_name = tempfile.mkstemp(prefix=".acceptance-evidence.", dir=root)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(
            json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
        )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _cas_metrics(release_root: Path, release_manifest: Mapping[str, Any]) -> dict[str, object]:
    entries = [
        item for component in release_manifest.get("components", []) for item in component.get("files", [])
    ]
    digests = {str(item["sha256"]) for item in entries}
    physical_bytes = sum(
        (release_root.parent / "objects" / digest[:2] / digest).stat().st_size for digest in digests
    )
    linked = 0
    for item in entries:
        digest = str(item["sha256"])
        materialized = release_root / str(item["path"])
        stored = release_root.parent / "objects" / digest[:2] / digest
        if stored.is_file() and os.path.samefile(materialized, stored):
            linked += 1
    return {
        "uniqueObjectCount": len(digests),
        "logicalBytes": sum(int(item.get("sizeBytes") or 0) for item in entries),
        "physicalBytes": physical_bytes,
        "materializedFileCount": len(entries),
        "hardlinkCount": linked,
        "copyCount": len(entries) - linked,
        "hardlinkRatio": linked / len(entries) if entries else 0.0,
    }


def _legacy_identity(source: Path) -> dict[str, object]:
    manifest = source / "dataset_manifest.json"
    if not manifest.is_file():
        return {"legacyDatasetId": "legacy", "sourceManifestSha256": None}
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    return {
        "legacyDatasetId": str(
            payload.get("version_id") or payload.get("dataset_id") or payload.get("sha256") or "legacy"
        ),
        "sourceManifestSha256": sha256_file(manifest),
    }


def _qlib_symbol(value: object) -> str:
    normalized = str(value).strip().upper()
    if "." in normalized:
        code, exchange = normalized.split(".", 1)
        return f"{exchange}{code}"
    return normalized


def _ohlc_suspension_quality(release: Any) -> dict[str, object]:
    missing_keys: set[tuple[str, str]] = set()
    explained_paused = 0
    unexplained = 0
    for path in release.files("qlib_staging"):
        names = set(pq.read_schema(path).names)
        required = {"date", "symbol", "open", "close", "paused"}
        if not required.issubset(names):
            raise ValueError(f"qlib_staging OHLC QA is missing columns: {sorted(required - names)}")
        frame = pd.read_parquet(path, columns=sorted(required))
        missing = frame[["open", "close"]].isna().any(axis=1)
        if not missing.any():
            continue
        paused = pd.to_numeric(frame["paused"], errors="coerce").fillna(0).ge(0.5)
        explained_paused += int((missing & paused).sum())
        unexplained += int((missing & ~paused).sum())
        rows = frame.loc[missing, ["date", "symbol"]]
        missing_keys.update(
            (
                str(pd.Timestamp(row.date).date()),
                _qlib_symbol(row.symbol),
            )
            for row in rows.itertuples(index=False)
        )
    confirmed: set[tuple[str, str]] = set()
    if missing_keys:
        for path in release.files("trade_status"):
            names = set(pq.read_schema(path).names)
            date_column = next((name for name in ("trade_date", "date", "cal_date") if name in names), None)
            symbol_column = next(
                (name for name in ("ts_code", "symbol", "instrument") if name in names), None
            )
            if date_column is None or symbol_column is None:
                continue
            frame = pd.read_parquet(path, columns=[date_column, symbol_column])
            for row in frame.itertuples(index=False, name=None):
                key = (str(pd.Timestamp(row[0]).date()), _qlib_symbol(row[1]))
                if key in missing_keys:
                    confirmed.add(key)
    return {
        "missingOpenOrClose": len(missing_keys),
        "explainedByPaused": explained_paused,
        "confirmedByTradeStatus": len(confirmed),
        "unexplainedMissingOhlc": unexplained,
        "passed": unexplained == 0,
    }


def run_migration_acceptance(
    settings: Settings,
    *,
    source_kind: str,
    source_root: str | Path,
    acceptance_root: str | Path,
    start: str | None = None,
    end: str | None = None,
    single_thread: bool = True,
) -> Path:
    source = Path(source_root).expanduser().resolve()
    target = Path(acceptance_root).expanduser().resolve()
    _assert_isolated(source, target)
    platform_revision = git_revision(Path(__file__).resolve().parents[2])
    qlib_revision = git_revision(resolve_qlib_repo(settings.qlib_repo))
    lineage_complete = bool(
        platform_revision.get("commit")
        and platform_revision.get("dirty") is False
        and qlib_revision.get("commit")
        and qlib_revision.get("dirty") is False
    )
    if source_kind == "research" and not lineage_complete:
        raise ValueError("research migration acceptance requires clean qlib-platform and Qlib worktrees")
    target.mkdir(parents=True, exist_ok=True)
    isolated = _isolated_settings(settings, target)
    isolated.paths.mkdirs()
    receipts = isolated.paths.state / "verification_receipts"
    started = time.perf_counter()
    verified_cas_digests: set[str] = set()

    if source_kind == "qlib":
        source_identity = _legacy_identity(source)
        release, dataset = import_qlib_dataset(isolated, source)
        ohlc_quality: dict[str, object] = {"status": "NOT_APPLICABLE"}
    elif source_kind == "research":
        if not start or not end:
            raise ValueError("research migration acceptance requires explicit --start and --end")
        source_settings = _source_settings(settings, source, target)
        release = LocalReleasePublisher(target / "releases").publish(
            profile=QLIB_RESEARCH_PROFILE_V2,
            components=local_research_components(source_settings),
            coverage={"start": str(start), "end": str(end)},
            policies={
                "governanceLevel": "research",
                "pitAvailability": "next_trading_day",
                "promotionAllowed": True,
            },
            lineage={"producer": "qlib-platform", "sourceType": "isolated_local_raw_migration"},
        )
        source_identity = {
            "legacyDatasetId": str(settings.qlib_dataset_name),
            "sourceManifestSha256": release.manifest_sha256,
        }
        isolated.data["data_source"] = {
            "kind": "data_release",
            "data_release": {
                "id": release.data_release_id,
                "data_root": str(target / "releases"),
                "manifest": str(release.manifest_path),
            },
        }
        release_verification: dict[str, object] = {}
        verify_data_release(
            target / "releases",
            release.manifest_path,
            configured_id=release.data_release_id,
            mode="deep",
            receipt_dir=receipts,
            evidence=release_verification,
            verified_digests=verified_cas_digests,
            workers=4,
        )
        ohlc_quality = _ohlc_suspension_quality(release)
        if not bool(ohlc_quality["passed"]):
            raise ValueError(
                f"migration acceptance found unexplained OHLC gaps: {ohlc_quality['unexplainedMissingOhlc']}"
            )
        materialize_data_release(isolated)
        dataset_path = dump_full(
            isolated,
            single_thread=single_thread,
            sync_context={
                "data_release_id": release.data_release_id,
                "data_release_manifest_sha256": release.manifest_sha256,
                "dataset_parents": [{"version_id": release.data_release_id, "relation": "converted_from"}],
            },
            promote_alias=False,
        )
        manifest_path = dataset_path / "dataset_manifest.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        registry = DatasetRegistry(isolated.registry_path)
        registry.register_release(release, governance_level="research")
        registry.promote_research_snapshot(
            release_alias="research-release-current",
            data_release_id=release.data_release_id,
            dataset_alias=isolated.qlib_dataset_ref,
            dataset_version_id=str(payload["version_id"]),
        )
        resolved_dataset = registry.get_version(str(payload["version_id"]))
        if resolved_dataset is None:
            raise RuntimeError("isolated migration did not register its DatasetVersion")
        dataset = resolved_dataset
    else:
        raise ValueError(f"unsupported migration source kind: {source_kind}")

    migration_seconds = time.perf_counter() - started
    release_evidence: dict[str, object] = {}
    release_started = time.perf_counter()
    verify_data_release(
        target / "releases",
        release.manifest_path,
        configured_id=release.data_release_id,
        mode="deep",
        receipt_dir=receipts,
        reuse_receipt=source_kind == "research",
        evidence=release_evidence,
        verified_digests=verified_cas_digests,
        workers=4,
    )
    release_verify_seconds = time.perf_counter() - release_started
    dataset_evidence: dict[str, object] = {}
    dataset_started = time.perf_counter()
    verify_dataset_manifest(
        dataset.manifest_path,
        mode="deep",
        receipt_dir=receipts,
        evidence=dataset_evidence,
        cas_root=target / "releases" / "objects",
        verified_digests=verified_cas_digests,
        workers=4,
    )
    dataset_manifest = json.loads(dataset.manifest_path.read_text(encoding="utf-8"))
    if (
        dataset_manifest.get("data_release_id") != release.data_release_id
        or dataset_manifest.get("data_release_manifest_sha256") != release.manifest_sha256
    ):
        raise ValueError("migrated DatasetVersion is not bound to the exact DataRelease")
    platform_matches_dataset = dataset_manifest.get("qlib_platform_git_commit") == platform_revision.get(
        "commit"
    )
    qlib_matches_dataset = dataset_manifest.get("qlib_git_commit") == qlib_revision.get("commit")
    if source_kind == "research" and (
        not platform_matches_dataset
        or not qlib_matches_dataset
        or dataset_manifest.get("qlib_platform_git_dirty") is not False
        or dataset_manifest.get("qlib_git_dirty") is not False
    ):
        raise ValueError("migrated DatasetVersion lineage does not match the clean runtime commits")
    dataset_verify_seconds = time.perf_counter() - dataset_started
    evidence: dict[str, object] = {
        "schemaVersion": "1.0",
        "acceptanceRoot": str(target),
        "sourceRoot": str(source),
        "sourceReadOnlyContract": True,
        "networkAccessAllowed": False,
        "lineage": {
            "qlibPlatformCommit": platform_revision.get("commit"),
            "qlibPlatformDirty": platform_revision.get("dirty"),
            "qlibCommit": qlib_revision.get("commit"),
            "qlibDirty": qlib_revision.get("dirty"),
            "complete": lineage_complete,
            "qlibPlatformCommitMatchesDataset": (
                platform_matches_dataset if source_kind == "research" else None
            ),
            "qlibCommitMatchesDataset": qlib_matches_dataset if source_kind == "research" else None,
        },
        "sourceKind": source_kind,
        **source_identity,
        "dataReleaseId": release.data_release_id,
        "dataReleaseManifestSha256": release.manifest_sha256,
        "dataReleaseProfile": release.profile,
        "datasetVersionId": dataset.version_id,
        "migrationWallSeconds": migration_seconds,
        "releaseVerifySeconds": release_verify_seconds,
        "datasetVerifySeconds": dataset_verify_seconds,
        "releaseVerification": release_evidence,
        "datasetVerification": dataset_evidence,
        "cas": _cas_metrics(release.manifest_path.parent, release.manifest),
        "ohlcSuspensionQuality": ohlc_quality,
        "downstream": {
            "trainRunId": None,
            "predictionSnapshotId": None,
            "backtestRunId": None,
            "researchAudit": "PENDING",
        },
    }
    return _write_evidence(target, evidence)
