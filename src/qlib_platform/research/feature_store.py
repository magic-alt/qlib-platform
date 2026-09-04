from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from qlib_platform.alpha.registry import alpha_pack_from_settings, assert_alpha_pack_compatible, handler_class
from qlib_platform.datasets.dataset_resolver import pin_dataset
from qlib_platform.lineage import git_revision, resolve_qlib_repo, sha256_json
from qlib_platform.runtime.runtime_safety import resolve_qlib_parallel_runtime
from qlib_platform.settings import Settings
from qlib_platform.data.store import sha256_file

FEATURE_STORE_SCHEMA = "feature_snapshot_v1"
FEATURE_LOADER_CONTRACT = "qlib_raw_feature_loader_v2"


def _feature_store_config(settings: Settings) -> Mapping[str, Any]:
    research = settings.data.get("research", {})
    config = research.get("feature_store", {}) if isinstance(research, Mapping) else {}
    return config if isinstance(config, Mapping) else {}


def feature_store_enabled(settings: Settings) -> bool:
    return bool(_feature_store_config(settings).get("enabled", False))


def _dataset_snapshot(settings: Settings) -> dict[str, object]:
    path = settings.qlib_data_uri / "dataset_manifest.json"
    if not path.is_file():
        return {
            "sha256": None,
            "mode": None,
            "syncContext": None,
            "lastDate": None,
            "parents": None,
        }
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
        "parents": payload.get("parents"),
    }


def _contract(settings: Settings, start_time: str, end_time: str) -> dict[str, object]:
    del start_time, end_time
    package_root = Path(__file__).resolve().parent
    snapshot = _dataset_snapshot(settings)
    pack = alpha_pack_from_settings(settings)
    # Only feature-defining implementation belongs in the raw-feature recipe.
    # Resolve files from the installed canonical package rather than a repository
    # checkout path, so editable installs and built wheels bind the same logical
    # implementation after the provider-neutral namespace migration.
    implementation = [
        package_root / "custom_handler.py",
        package_root / "data" / "fundamentals.py",
        package_root / "alpha" / "registry.py",
    ]
    return {
        "schema": FEATURE_STORE_SCHEMA,
        "featureLoaderContract": FEATURE_LOADER_CONTRACT,
        "datasetId": snapshot.get("datasetId") or settings.qlib_data_uri.name,
        "datasetVersionId": snapshot.get("versionId") or snapshot.get("sha256"),
        "datasetManifestSha256": snapshot.get("manifestSha256"),
        "datasetFields": snapshot.get("fields"),
        "universe": settings.data.get("universe", {}),
        "alphaPack": pack.to_manifest(),
        "implementationSha256": {path.name: sha256_file(path) for path in implementation if path.is_file()},
        "qlibCommit": git_revision(resolve_qlib_repo(settings.qlib_repo)).get("commit"),
    }


def _semantic_contract(contract: Mapping[str, object]) -> dict[str, object]:
    return {
        key: contract.get(key)
        for key in (
            "schema",
            "featureLoaderContract",
            "datasetId",
            "datasetFields",
            "universe",
            "alphaPack",
            "implementationSha256",
            "qlibCommit",
        )
    }


def _semantic_recipe_id(contract: Mapping[str, object]) -> str:
    return "frs_" + sha256_json(_semantic_contract(contract))


def _dataset_identity(snapshot: Mapping[str, object]) -> str:
    return str(snapshot.get("versionId") or snapshot.get("sha256") or snapshot.get("manifestSha256") or "")


def _updated_from_parent_id(snapshot: Mapping[str, object]) -> str | None:
    parents = snapshot.get("parents")
    if not isinstance(parents, list):
        return None
    updated_from = [
        str(item.get("version_id") or item.get("versionId") or "").strip()
        for item in parents
        if isinstance(item, Mapping) and str(item.get("relation") or "") == "updated_from"
    ]
    updated_from = [value for value in updated_from if value]
    return updated_from[0] if len(updated_from) == 1 else None


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
    # A feature snapshot intentionally has no label payload. Passing the handler's
    # loader through here would retain the empty label group and recent Qlib versions
    # reject that group with ``ValueError: fields cannot be empty``. Build the loader
    # from the feature contract only so cold materialization has exactly one, explicit
    # raw-feature request.
    from qlib.data.dataset.loader import QlibDataLoader

    frame = QlibDataLoader({"feature": handler.get_feature_config()}).load(
        handler.instruments, start_time=start_time, end_time=end_time
    )
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


def _read_manifest(path: Path) -> dict[str, object]:
    payload = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"feature-snapshot manifest must be an object: {path}")
    return payload


def _coverage(manifest: Mapping[str, object]) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    coverage = manifest.get("coverage", {})
    if not isinstance(coverage, Mapping) or not coverage.get("startTime") or not coverage.get("endTime"):
        return None
    return pd.Timestamp(coverage["startTime"]), pd.Timestamp(coverage["endTime"])


def _snapshot_id(manifest: Mapping[str, object], path: Path) -> str:
    return str(manifest.get("featureSnapshotId") or path.name)


def _iter_snapshots(snapshots_root: Path):
    manifests = snapshots_root.glob("*/manifest.json") if snapshots_root.is_dir() else ()
    for manifest_path in manifests:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(manifest, Mapping) and manifest.get("schemaVersion") == FEATURE_STORE_SCHEMA:
            yield manifest_path.parent, manifest


def _write_feature_snapshot(
    snapshots_root: Path,
    recipe_id: str,
    contract: Mapping[str, object],
    snapshot: Mapping[str, object],
    frame: pd.DataFrame,
    *,
    cache_build: Mapping[str, object] | None = None,
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
            "featureSemanticId": _semantic_recipe_id(contract),
            "featureSnapshotId": snapshot_id,
            "contract": dict(contract),
            "datasetSnapshot": dict(snapshot),
            "coverage": coverage,
            "rows": len(frame),
            "columns": [str(column) for column in frame.columns],
            "files": files,
            "cacheBuild": dict(cache_build or {"mode": "MATERIALIZED"}),
        }
        (building / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
        )
        target = snapshots_root / snapshot_id
        if target.exists():
            load_feature_store(target, coverage["startTime"], coverage["endTime"], verify_checksums=True)
            return target
        try:
            os.replace(building, target)
        except OSError:
            # Concurrent materializers may race to publish identical content. If another
            # process won, trust it only after a full checksum verification; otherwise
            # propagate the filesystem failure.
            if not target.exists():
                raise
            load_feature_store(target, coverage["startTime"], coverage["endTime"], verify_checksums=True)
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
    expected_dataset = _dataset_identity(dataset_snapshot)
    for path, manifest in _iter_snapshots(snapshots_root):
        coverage = _coverage(manifest)
        recorded_dataset = manifest.get("datasetSnapshot", {})
        if (
            manifest.get("featureRecipeId") == recipe_id
            and coverage is not None
            and isinstance(recorded_dataset, Mapping)
            and _dataset_identity(recorded_dataset) == expected_dataset
            and coverage[0] <= pd.Timestamp(start_time)
            and coverage[1] >= pd.Timestamp(end_time)
        ):
            matches.append((coverage[1] - coverage[0], path))
    return min(matches, key=lambda item: item[0])[1] if matches else None


def _extendable_snapshot(
    snapshots_root: Path,
    recipe_id: str,
    start_time: str,
    end_time: str,
    dataset_snapshot: Mapping[str, object],
) -> Path | None:
    matches: list[tuple[pd.Timestamp, Path]] = []
    expected_dataset = _dataset_identity(dataset_snapshot)
    for path, manifest in _iter_snapshots(snapshots_root):
        coverage = _coverage(manifest)
        recorded_dataset = manifest.get("datasetSnapshot", {})
        if (
            manifest.get("featureRecipeId") == recipe_id
            and coverage is not None
            and isinstance(recorded_dataset, Mapping)
            and _dataset_identity(recorded_dataset) == expected_dataset
            and coverage[0] <= pd.Timestamp(start_time)
            and pd.Timestamp(start_time) <= coverage[1] < pd.Timestamp(end_time)
        ):
            matches.append((coverage[1], path))
    return max(matches, key=lambda item: item[0])[1] if matches else None


def _semantic_snapshot(
    snapshots_root: Path,
    semantic_id: str,
    dataset_version_id: str,
    start_time: str,
    required_end: str | pd.Timestamp,
) -> Path | None:
    matches: list[tuple[pd.Timestamp, Path]] = []
    for path, manifest in _iter_snapshots(snapshots_root):
        coverage = _coverage(manifest)
        recorded_dataset = manifest.get("datasetSnapshot", {})
        if (
            manifest.get("featureSemanticId") == semantic_id
            and coverage is not None
            and isinstance(recorded_dataset, Mapping)
            and _dataset_identity(recorded_dataset) == dataset_version_id
            and coverage[0] <= pd.Timestamp(start_time)
            and coverage[1] >= pd.Timestamp(required_end)
        ):
            matches.append((coverage[1], path))
    return max(matches, key=lambda item: item[0])[1] if matches else None


def _initialize_qlib(settings: Settings) -> None:
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


def _lookback_days(settings: Settings) -> int:
    configured = int(str(_feature_store_config(settings).get("append_lookback_trading_days", 0) or 0))
    return max(configured, alpha_pack_from_settings(settings).warmup_trading_days)


def _extend_same_dataset(
    settings: Settings,
    snapshots_root: Path,
    recipe_id: str,
    contract: Mapping[str, object],
    snapshot: Mapping[str, object],
    source: Path,
    start_time: str,
    end_time: str,
) -> tuple[Path, dict[str, object]]:
    source_manifest = _read_manifest(source)
    source_coverage = _coverage(source_manifest)
    if source_coverage is None:
        raise ValueError(f"feature snapshot has invalid coverage: {source}")
    _initialize_qlib(settings)
    recompute_start = _lookback_start(source_coverage[1], _lookback_days(settings))
    base = load_feature_store(source, start_time, str(source_coverage[1].date()), verify_checksums=True)
    replacement = _raw_features(settings, recompute_start, end_time)
    frame = _merge_recomputed(base, replacement).loc[pd.Timestamp(start_time) : pd.Timestamp(end_time)]
    target = _write_feature_snapshot(
        snapshots_root,
        recipe_id,
        contract,
        snapshot,
        frame,
        cache_build={
            "mode": "EXTENDED",
            "sourceFeatureSnapshotId": _snapshot_id(source_manifest, source),
            "recomputeStartTime": recompute_start,
        },
    )
    return target, {
        "cacheStatus": "EXTENDED",
        "rawMaterializationCalls": 1,
        "sourceFeatureSnapshotId": _snapshot_id(source_manifest, source),
        "recomputeStartTime": recompute_start,
    }


def _sync_values(sync_context: Mapping[str, object], snake: str, camel: str) -> tuple[str, ...]:
    raw = sync_context.get(snake, sync_context.get(camel, ()))
    if isinstance(raw, (list, tuple, set)):
        return tuple(str(value) for value in raw if str(value).strip())
    return ()


def _incremental_across_dataset_versions(
    settings: Settings,
    snapshots_root: Path,
    recipe_id: str,
    contract: Mapping[str, object],
    snapshot: Mapping[str, object],
    start_time: str,
    end_time: str,
) -> tuple[Path, dict[str, object]] | None:
    # Cross-version reuse is permitted only from the one direct DatasetVersion
    # predecessor recorded by qlib_export as ``updated_from``. The sync delta is
    # relative to that parent; using any older semantic match could omit intervening
    # historical revisions and would violate fail-closed lineage.
    parent_version_id = _updated_from_parent_id(snapshot)
    if parent_version_id is None:
        return None
    sync_context = snapshot.get("syncContext")
    if not isinstance(sync_context, Mapping):
        return None
    changed_dates = _sync_values(sync_context, "changed_trade_dates", "changedTradeDates")
    revised_symbols = _sync_values(sync_context, "revised_symbols", "revisedSymbols")
    if revised_symbols or not changed_dates or bool(sync_context.get("pit_changed", False)):
        return None
    try:
        first_changed = min(pd.Timestamp(value) for value in changed_dates)
    except (TypeError, ValueError):
        return None

    semantic_id = _semantic_recipe_id(contract)
    if first_changed > pd.Timestamp(end_time):
        source = _semantic_snapshot(snapshots_root, semantic_id, parent_version_id, start_time, end_time)
        if source is None:
            return None
        source_manifest = _read_manifest(source)
        frame = load_feature_store(source, start_time, end_time, verify_checksums=True)
        target = _write_feature_snapshot(
            snapshots_root,
            recipe_id,
            contract,
            snapshot,
            frame,
            cache_build={
                "mode": "REBOUND",
                "sourceFeatureSnapshotId": _snapshot_id(source_manifest, source),
                "sourceDatasetVersionId": parent_version_id,
                "firstChangedTradeDate": str(first_changed.date()),
            },
        )
        return target, {
            "cacheStatus": "REBOUND",
            "rawMaterializationCalls": 0,
            "sourceFeatureSnapshotId": _snapshot_id(source_manifest, source),
            "sourceDatasetVersionId": parent_version_id,
            "firstChangedTradeDate": str(first_changed.date()),
        }

    _initialize_qlib(settings)
    recompute_start = _lookback_start(first_changed, _lookback_days(settings))
    if pd.Timestamp(recompute_start) <= pd.Timestamp(start_time):
        return None
    source = _semantic_snapshot(snapshots_root, semantic_id, parent_version_id, start_time, recompute_start)
    if source is None:
        return None
    source_manifest = _read_manifest(source)
    source_coverage = _coverage(source_manifest)
    if source_coverage is None:
        return None
    base_end = min(source_coverage[1], pd.Timestamp(end_time))
    base = load_feature_store(source, start_time, str(base_end.date()), verify_checksums=True)
    replacement = _raw_features(settings, recompute_start, end_time)
    frame = _merge_recomputed(base, replacement).loc[pd.Timestamp(start_time) : pd.Timestamp(end_time)]
    target = _write_feature_snapshot(
        snapshots_root,
        recipe_id,
        contract,
        snapshot,
        frame,
        cache_build={
            "mode": "INCREMENTAL",
            "sourceFeatureSnapshotId": _snapshot_id(source_manifest, source),
            "sourceDatasetVersionId": parent_version_id,
            "recomputeStartTime": recompute_start,
            "firstChangedTradeDate": str(first_changed.date()),
        },
    )
    return target, {
        "cacheStatus": "INCREMENTAL",
        "rawMaterializationCalls": 1,
        "sourceFeatureSnapshotId": _snapshot_id(source_manifest, source),
        "sourceDatasetVersionId": parent_version_id,
        "recomputeStartTime": recompute_start,
        "firstChangedTradeDate": str(first_changed.date()),
    }


def _materialize_feature_store(
    settings: Settings, start_time: str, end_time: str, *, force: bool = False
) -> tuple[Path, dict[str, object]]:
    settings, _ = pin_dataset(settings)
    contract = _contract(settings, start_time, end_time)
    recipe_id = "fr_" + sha256_json(contract)
    snapshots_root = _store_root(settings) / "snapshots"
    snapshot = _dataset_snapshot(settings)

    if not force:
        reusable = _reusable_snapshot(snapshots_root, recipe_id, start_time, end_time, snapshot)
        if reusable is not None:
            load_feature_store(reusable, start_time, end_time, verify_checksums=True)
            return reusable, {"cacheStatus": "REUSED", "rawMaterializationCalls": 0}

        extendable = _extendable_snapshot(snapshots_root, recipe_id, start_time, end_time, snapshot)
        if extendable is not None:
            return _extend_same_dataset(
                settings,
                snapshots_root,
                recipe_id,
                contract,
                snapshot,
                extendable,
                start_time,
                end_time,
            )

        incremental = _incremental_across_dataset_versions(
            settings,
            snapshots_root,
            recipe_id,
            contract,
            snapshot,
            start_time,
            end_time,
        )
        if incremental is not None:
            return incremental

    _initialize_qlib(settings)
    frame = _raw_features(settings, start_time, end_time)
    path = _write_feature_snapshot(
        snapshots_root,
        recipe_id,
        contract,
        snapshot,
        frame,
        cache_build={"mode": "MATERIALIZED", "forced": bool(force)},
    )
    return path, {
        "cacheStatus": "MATERIALIZED",
        "rawMaterializationCalls": 1,
        "forced": bool(force),
    }


def materialize_feature_store(
    settings: Settings, start_time: str, end_time: str, *, force: bool = False
) -> Path:
    path, _ = _materialize_feature_store(settings, start_time, end_time, force=force)
    return path


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
    path, cache_evidence = _materialize_feature_store(settings, start_time, end_time, force=force)
    frame = load_feature_store(path, start_time, end_time)
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    return frame, {
        "featureRecipeId": manifest["featureRecipeId"],
        "featureSemanticId": manifest.get("featureSemanticId"),
        "featureSnapshotId": manifest["featureSnapshotId"],
        "datasetVersionId": manifest.get("contract", {}).get("datasetVersionId"),
        "path": str(path),
        "rows": len(frame),
        "manifestSha256": sha256_file(path / "manifest.json"),
        **cache_evidence,
    }


materialize_feature_snapshot = materialize_feature_store
load_feature_snapshot = load_feature_store
