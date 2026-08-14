from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
from .alpha.registry import alpha_pack_from_settings, assert_alpha_pack_compatible, handler_class

from .dataset_resolver import pin_dataset
from .lineage import git_revision, resolve_qlib_repo, sha256_json
from .runtime_safety import resolve_qlib_parallel_runtime
from .settings import Settings
from .store import sha256_file

FEATURE_STORE_SCHEMA = "feature_snapshot_v1"


def _feature_store_config(settings: Settings) -> Mapping[str, Any]:
    research = settings.data.get("research", {})
    config = research.get("feature_store", {}) if isinstance(research, Mapping) else {}
    return config if isinstance(config, Mapping) else {}


def feature_store_enabled(settings: Settings) -> bool:
    return bool(_feature_store_config(settings).get("enabled", False))


def _dataset_snapshot(settings: Settings) -> dict[str, object]:
    path = settings.qlib_data_uri / "dataset_manifest.json"
    if not path.is_file():
        return {"sha256": None, "mode": None, "syncContext": None, "lastDate": None}
    payload = json.loads(path.read_text(encoding="utf-8"))
    smoke = payload.get("smoke_test", {})
    return {
        "sha256": str(payload.get("sha256") or sha256_file(path)),
        "mode": payload.get("mode"),
        "syncContext": payload.get("sync_context"),
        "lastDate": smoke.get("last_date") if isinstance(smoke, Mapping) else None,
        "datasetId": payload.get("dataset_id"),
        "versionId": payload.get("version_id") or payload.get("sha256"),
        "manifestSha256": sha256_file(path),
        "fields": payload.get("fields"),
    }


def _contract(settings: Settings, start_time: str, end_time: str) -> dict[str, object]:
    project_root = Path(__file__).resolve().parents[2]
    snapshot = _dataset_snapshot(settings)
    pack = alpha_pack_from_settings(settings)
    implementation = [
        project_root / "src" / "tushare_qlib" / "custom_handler.py",
        project_root / "src" / "tushare_qlib" / "processors.py",
        project_root / "src" / "tushare_qlib" / "feature_store.py",
    ]
    return {
        "schema": FEATURE_STORE_SCHEMA,
        "datasetId": snapshot.get("datasetId") or settings.qlib_data_uri.name,
        "datasetVersionId": snapshot.get("versionId") or snapshot.get("sha256"),
        "datasetManifestSha256": snapshot.get("manifestSha256"),
        "datasetFields": snapshot.get("fields"),
        "universe": settings.data.get("universe", {}),
        "alphaPack": pack.to_manifest(),
        "processorRecipeId": pack.processor_recipe,
        "implementationSha256": {path.name: sha256_file(path) for path in implementation if path.is_file()},
        "qlibCommit": git_revision(resolve_qlib_repo(settings.qlib_repo)).get("commit"),
    }


def _store_root(settings: Settings) -> Path:
    configured = _feature_store_config(settings).get("root")
    if configured:
        path = Path(str(configured)).expanduser()
        return path if path.is_absolute() else (settings.config_path.parent / path).resolve()
    return settings.paths.root / "cache" / "features"


def _raw_features(
    settings: Settings,
    start_time: str,
    end_time: str,
    *,
    instruments: object | None = None,
) -> pd.DataFrame:
    universe = settings.data.get("universe", {})
    pack = alpha_pack_from_settings(settings)
    assert_alpha_pack_compatible(settings, pack)
    handler = handler_class(pack)(
        instruments=instruments or universe.get("instruments", "all"),
        start_time=start_time,
        end_time=end_time,
        fit_start_time=start_time,
        fit_end_time=start_time,
        label=([], []),
        shared_processors=[],
        infer_processors=[],
        learn_processors=[],
        init_data=False,
    )
    frame = handler.data_loader.load(handler.instruments, start_time=start_time, end_time=end_time)
    if frame.empty:
        raise RuntimeError("Qlib returned no rows while materializing the research feature store")
    if frame.index.names != ["datetime", "instrument"]:
        frame.index = frame.index.set_names(["datetime", "instrument"])
    return frame.sort_index()


def _lookback_start(value: str | pd.Timestamp, trading_days: int) -> str:
    from qlib.data import D

    calendar = pd.DatetimeIndex(D.calendar(end_time=pd.Timestamp(value), freq="day"))
    if calendar.empty:
        return str(pd.Timestamp(value).date())
    position = max(0, len(calendar) - max(1, trading_days) - 1)
    return str(calendar[position].date())


def _merge_recomputed(base: pd.DataFrame, replacement: pd.DataFrame) -> pd.DataFrame:
    if replacement.empty:
        return base
    retained = base.loc[~base.index.isin(replacement.index)]
    return pd.concat([retained, replacement]).sort_index()


def _write_feature_snapshot(
    snapshots_root: Path,
    recipe_id: str,
    contract: Mapping[str, object],
    snapshot: Mapping[str, object],
    frame: pd.DataFrame,
) -> Path:
    snapshots_root.mkdir(parents=True, exist_ok=True)
    building = Path(tempfile.mkdtemp(prefix=".building.", dir=snapshots_root))
    try:
        if isinstance(frame.columns, pd.MultiIndex) and "label" in frame.columns.get_level_values(0):
            frame = frame.drop(columns="label", level=0)
        files: list[dict[str, object]] = []
        datetimes = pd.DatetimeIndex(frame.index.get_level_values("datetime"))
        for year in sorted(datetimes.year.unique()):
            partition = frame.loc[datetimes.year == year]
            path = building / f"year={int(year)}.parquet"
            partition.to_parquet(path)
            files.append({"name": path.name, "rows": len(partition), "sha256": sha256_file(path)})
        coverage = {
            "startTime": str(pd.Timestamp(datetimes.min()).date()),
            "endTime": str(pd.Timestamp(datetimes.max()).date()),
        }
        snapshot_id = "fs_" + sha256_json(
            {"featureRecipeId": recipe_id, "coverage": coverage, "files": files}
        )
        manifest = {
            "schemaVersion": FEATURE_STORE_SCHEMA,
            "featureRecipeId": recipe_id,
            "featureSnapshotId": snapshot_id,
            "contract": dict(contract),
            "datasetSnapshot": dict(snapshot),
            "coverage": coverage,
            "rows": len(frame),
            "columns": [str(column) for column in frame.columns],
            "files": files,
        }
        (building / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
        )
        target = snapshots_root / snapshot_id
        if target.exists():
            load_feature_store(target, coverage["startTime"], coverage["endTime"], verify_checksums=True)
            return target
        os.replace(building, target)
        return target
    finally:
        if building.exists():
            shutil.rmtree(building, ignore_errors=True)


def _reusable_snapshot(
    snapshots_root: Path,
    recipe_id: str,
    start_time: str,
    end_time: str,
    dataset_snapshot: Mapping[str, object],
) -> Path | None:
    matches: list[tuple[pd.Timedelta, Path]] = []
    manifests = snapshots_root.glob("*/manifest.json") if snapshots_root.is_dir() else ()
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        coverage = manifest.get("coverage", {})
        recorded_dataset = manifest.get("datasetSnapshot", {})
        if (
            manifest.get("schemaVersion") == FEATURE_STORE_SCHEMA
            and manifest.get("featureRecipeId") == recipe_id
            and isinstance(coverage, Mapping)
            and isinstance(recorded_dataset, Mapping)
            and recorded_dataset.get("sha256") == dataset_snapshot.get("sha256")
            and pd.Timestamp(coverage.get("startTime")) <= pd.Timestamp(start_time)
            and pd.Timestamp(coverage.get("endTime")) >= pd.Timestamp(end_time)
        ):
            span = pd.Timestamp(coverage["endTime"]) - pd.Timestamp(coverage["startTime"])
            matches.append((span, manifest_path.parent))
    return min(matches, key=lambda item: item[0])[1] if matches else None


def materialize_feature_store(
    settings: Settings, start_time: str, end_time: str, *, force: bool = False
) -> Path:
    settings, _ = pin_dataset(settings)
    contract = _contract(settings, start_time, end_time)
    recipe_id = "fr_" + sha256_json(contract)
    # Keep recipe and snapshot identities in the manifest instead of nesting two
    # full SHA-256 directory names. This stays below Windows' common path limit.
    snapshots_root = _store_root(settings) / "snapshots"
    snapshot = _dataset_snapshot(settings)
    reusable = (
        None if force else _reusable_snapshot(snapshots_root, recipe_id, start_time, end_time, snapshot)
    )
    if reusable is not None:
        load_feature_store(reusable, start_time, end_time, verify_checksums=True)
        return reusable

    import qlib
    from qlib.constant import REG_CN

    parallel = resolve_qlib_parallel_runtime(settings)
    qlib.init(
        provider_uri=str(settings.qlib_data_uri),
        region=REG_CN,
        expression_cache=None,
        dataset_cache=None,
        **parallel.qlib_init_kwargs(),
    )
    frame = _raw_features(settings, start_time, end_time)
    return _write_feature_snapshot(snapshots_root, recipe_id, contract, snapshot, frame)


def load_feature_store(
    path: Path,
    start_time: str,
    end_time: str,
    *,
    verify_checksums: bool = False,
) -> pd.DataFrame:
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"feature-snapshot manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != FEATURE_STORE_SCHEMA:
        raise ValueError(f"unsupported feature-snapshot schema: {manifest.get('schemaVersion')}")
    frames: list[pd.DataFrame] = []
    requested_years = set(range(pd.Timestamp(start_time).year, pd.Timestamp(end_time).year + 1))
    for entry in manifest.get("files", []):
        file_path = path / str(entry["name"])
        match = str(entry["name"]).removeprefix("year=").removesuffix(".parquet")
        if match.isdigit() and int(match) not in requested_years:
            continue
        if not file_path.is_file() or (verify_checksums and sha256_file(file_path) != entry.get("sha256")):
            raise ValueError(f"feature-store partition checksum mismatch: {file_path}")
        frames.append(pd.read_parquet(file_path))
    if not frames:
        raise ValueError(f"feature snapshot contains no partitions: {path}")
    frame = pd.concat(frames).sort_index()
    return frame.loc[pd.Timestamp(start_time) : pd.Timestamp(end_time)]


def prepare_feature_data(
    settings: Settings, start_time: str, end_time: str, *, force: bool = False
) -> tuple[pd.DataFrame, dict[str, object]]:
    path = materialize_feature_store(settings, start_time, end_time, force=force)
    frame = load_feature_store(path, start_time, end_time)
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    return frame, {
        "featureRecipeId": manifest["featureRecipeId"],
        "featureSnapshotId": manifest["featureSnapshotId"],
        "datasetVersionId": manifest.get("contract", {}).get("datasetVersionId"),
        "path": str(path),
        "rows": len(frame),
        "manifestSha256": sha256_file(path / "manifest.json"),
    }


materialize_feature_snapshot = materialize_feature_store
load_feature_snapshot = load_feature_store
