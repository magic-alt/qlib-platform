from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from .lineage import git_revision, resolve_qlib_repo, sha256_json
from .research_timing import label_timing_from_settings
from .runtime_safety import resolve_qlib_parallel_runtime
from .settings import Settings
from .store import sha256_file

FEATURE_STORE_SCHEMA = "research_features_v2"


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
        "fields": payload.get("fields"),
    }


def _contract(settings: Settings, start_time: str, end_time: str) -> dict[str, object]:
    del start_time, end_time
    project_root = Path(__file__).resolve().parents[2]
    snapshot = _dataset_snapshot(settings)
    implementation = [
        project_root / "src" / "tushare_qlib" / "custom_handler.py",
        project_root / "src" / "tushare_qlib" / "processors.py",
        project_root / "src" / "tushare_qlib" / "feature_store.py",
    ]
    return {
        "schema": FEATURE_STORE_SCHEMA,
        "datasetId": snapshot.get("datasetId") or settings.qlib_data_uri.name,
        "datasetFields": snapshot.get("fields"),
        "labelTiming": label_timing_from_settings(settings).to_manifest(),
        "universe": settings.data.get("universe", {}),
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
    from .custom_handler import TushareAlpha158Fundamental

    universe = settings.data.get("universe", {})
    timing = label_timing_from_settings(settings)
    handler = TushareAlpha158Fundamental(
        instruments=instruments or universe.get("instruments", "all"),
        start_time=start_time,
        end_time=end_time,
        fit_start_time=start_time,
        fit_end_time=start_time,
        label=([f"Ref($close, -{timing.lookahead_days})/Ref($close, -1) - 1"], ["LABEL0"]),
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


def _write_feature_store(
    root: Path,
    target: Path,
    store_id: str,
    contract: Mapping[str, object],
    snapshot: Mapping[str, object],
    frame: pd.DataFrame,
) -> Path:
    building = Path(tempfile.mkdtemp(prefix=f".{store_id}.building.", dir=root))
    try:
        files: list[dict[str, object]] = []
        datetimes = pd.DatetimeIndex(frame.index.get_level_values("datetime"))
        for year in sorted(datetimes.year.unique()):
            partition = frame.loc[datetimes.year == year]
            path = building / f"year={int(year)}.parquet"
            partition.to_parquet(path)
            files.append({"name": path.name, "rows": len(partition), "sha256": sha256_file(path)})
        manifest = {
            "schemaVersion": FEATURE_STORE_SCHEMA,
            "featureStoreId": store_id,
            "contract": dict(contract),
            "datasetSnapshot": dict(snapshot),
            "coverage": {
                "startTime": str(pd.Timestamp(datetimes.min()).date()),
                "endTime": str(pd.Timestamp(datetimes.max()).date()),
            },
            "rows": len(frame),
            "columns": [str(column) for column in frame.columns],
            "files": files,
        }
        (building / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        replaced: Path | None = None
        if target.exists():
            replaced = root / f".{store_id}.replaced.{os.getpid()}"
            if replaced.exists():
                shutil.rmtree(replaced)
            os.replace(target, replaced)
        try:
            os.replace(building, target)
        except Exception:
            if replaced is not None and replaced.exists() and not target.exists():
                os.replace(replaced, target)
            raise
        else:
            if replaced is not None:
                shutil.rmtree(replaced, ignore_errors=True)
    finally:
        if building.exists():
            shutil.rmtree(building, ignore_errors=True)
    return target


def materialize_feature_store(
    settings: Settings, start_time: str, end_time: str, *, force: bool = False
) -> Path:
    contract = _contract(settings, start_time, end_time)
    store_id = sha256_json(contract)[:32]
    root = _store_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    target = root / store_id
    manifest_path = target / "manifest.json"
    snapshot = _dataset_snapshot(settings)
    existing: dict[str, object] | None = None
    if manifest_path.is_file():
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        if loaded.get("schemaVersion") == FEATURE_STORE_SCHEMA:
            existing = loaded
            coverage = loaded.get("coverage", {})
            previous_snapshot = loaded.get("datasetSnapshot", {})
            if (
                not force
                and isinstance(coverage, Mapping)
                and isinstance(previous_snapshot, Mapping)
                and previous_snapshot.get("sha256") == snapshot.get("sha256")
                and pd.Timestamp(coverage.get("startTime")) <= pd.Timestamp(start_time)
                and pd.Timestamp(coverage.get("endTime")) >= pd.Timestamp(end_time)
            ):
                return target

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
    frame: pd.DataFrame | None = None
    if existing is not None and not force:
        coverage = existing.get("coverage", {})
        sync_context = snapshot.get("syncContext")
        mode = str(snapshot.get("mode") or "")
        safe_delta = mode in {"update", "repair", "update_fix"} and isinstance(sync_context, Mapping)
        if (
            safe_delta
            and isinstance(coverage, Mapping)
            and pd.Timestamp(coverage.get("startTime")) <= pd.Timestamp(start_time)
        ):
            assert isinstance(sync_context, Mapping)
            cached_start = str(coverage["startTime"])
            cached_end = str(coverage["endTime"])
            frame = load_feature_store(target, cached_start, cached_end)
            changed = [
                pd.Timestamp(value)
                for value in sync_context.get("changed_trade_dates", [])
                if pd.Timestamp(value) <= pd.Timestamp(end_time)
            ]
            refresh_points = list(changed)
            if pd.Timestamp(end_time) > pd.Timestamp(cached_end):
                refresh_points.append(pd.Timestamp(cached_end))
            if refresh_points:
                config = _feature_store_config(settings)
                lookback = int(config.get("append_lookback_trading_days", 120))
                refresh_start = _lookback_start(min(refresh_points), lookback)
                replacement = _raw_features(settings, refresh_start, end_time)
                frame = _merge_recomputed(frame, replacement)
            revised = [str(value) for value in sync_context.get("revised_symbols", []) if value]
            if revised:
                replacement = _raw_features(
                    settings, min(str(start_time), cached_start), end_time, instruments=revised
                )
                frame = _merge_recomputed(frame, replacement)
            frame = frame.loc[pd.Timestamp(min(str(start_time), cached_start)) : pd.Timestamp(end_time)]
    if frame is None:
        frame = _raw_features(settings, start_time, end_time)
    return _write_feature_store(root, target, store_id, contract, snapshot, frame)


def load_feature_store(
    path: Path,
    start_time: str,
    end_time: str,
    *,
    verify_checksums: bool = False,
) -> pd.DataFrame:
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"feature-store manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != FEATURE_STORE_SCHEMA:
        raise ValueError(f"unsupported feature-store schema: {manifest.get('schemaVersion')}")
    frames: list[pd.DataFrame] = []
    requested_years = set(range(pd.Timestamp(start_time).year, pd.Timestamp(end_time).year + 1))
    for entry in manifest.get("files", []):
        file_path = path / str(entry["name"])
        match = str(entry["name"]).removeprefix("year=").removesuffix(".parquet")
        if match.isdigit() and int(match) not in requested_years:
            continue
        if not file_path.is_file() or (
            verify_checksums and sha256_file(file_path) != entry.get("sha256")
        ):
            raise ValueError(f"feature-store partition checksum mismatch: {file_path}")
        frames.append(pd.read_parquet(file_path))
    if not frames:
        raise ValueError(f"feature store contains no partitions: {path}")
    frame = pd.concat(frames).sort_index()
    return frame.loc[pd.Timestamp(start_time) : pd.Timestamp(end_time)]


def prepare_feature_data(
    settings: Settings, start_time: str, end_time: str, *, force: bool = False
) -> tuple[pd.DataFrame, dict[str, object]]:
    path = materialize_feature_store(settings, start_time, end_time, force=force)
    frame = load_feature_store(path, start_time, end_time)
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    return frame, {
        "featureStoreId": manifest["featureStoreId"],
        "path": str(path),
        "rows": len(frame),
        "manifestSha256": sha256_file(path / "manifest.json"),
    }
