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
from .settings import Settings
from .store import sha256_file
from .universe import membership_fingerprint

FEATURE_STORE_SCHEMA = "research_features_v1"


def _feature_store_config(settings: Settings) -> Mapping[str, Any]:
    research = settings.data.get("research", {})
    config = research.get("feature_store", {}) if isinstance(research, Mapping) else {}
    return config if isinstance(config, Mapping) else {}


def feature_store_enabled(settings: Settings) -> bool:
    return bool(_feature_store_config(settings).get("enabled", False))


def _contract(settings: Settings, start_time: str, end_time: str) -> dict[str, object]:
    project_root = Path(__file__).resolve().parents[2]
    manifest = settings.qlib_data_uri / "dataset_manifest.json"
    implementation = [
        project_root / "src" / "tushare_qlib" / "custom_handler.py",
        project_root / "src" / "tushare_qlib" / "processors.py",
        project_root / "src" / "tushare_qlib" / "feature_store.py",
    ]
    return {
        "schema": FEATURE_STORE_SCHEMA,
        "startTime": str(pd.Timestamp(start_time).date()),
        "endTime": str(pd.Timestamp(end_time).date()),
        "datasetManifestSha256": sha256_file(manifest) if manifest.is_file() else None,
        "universeMembershipSha256": membership_fingerprint(settings),
        "labelTiming": label_timing_from_settings(settings).to_manifest(),
        "universe": settings.data.get("universe", {}),
        "implementationSha256": {path.name: sha256_file(path) for path in implementation if path.is_file()},
        "qlibPlatformCommit": git_revision(project_root).get("commit"),
        "qlibCommit": git_revision(resolve_qlib_repo(settings.qlib_repo)).get("commit"),
    }


def _store_root(settings: Settings) -> Path:
    configured = _feature_store_config(settings).get("root")
    if configured:
        path = Path(str(configured)).expanduser()
        return path if path.is_absolute() else (settings.config_path.parent / path).resolve()
    return settings.paths.root / "cache" / "features"


def _raw_features(settings: Settings, start_time: str, end_time: str) -> pd.DataFrame:
    from .custom_handler import TushareAlpha158Fundamental

    universe = settings.data.get("universe", {})
    timing = label_timing_from_settings(settings)
    handler = TushareAlpha158Fundamental(
        instruments=universe.get("instruments", "all"),
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


def materialize_feature_store(
    settings: Settings, start_time: str, end_time: str, *, force: bool = False
) -> Path:
    contract = _contract(settings, start_time, end_time)
    store_id = sha256_json(contract)[:32]
    root = _store_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    target = root / store_id
    manifest_path = target / "manifest.json"
    if manifest_path.is_file() and not force:
        return target

    import qlib
    from qlib.constant import REG_CN

    research = settings.data.get("research", {})
    kernels = int(research.get("qlib_kernels", 4)) if isinstance(research, Mapping) else 4
    qlib.init(
        provider_uri=str(settings.qlib_data_uri),
        region=REG_CN,
        expression_cache=None,
        dataset_cache=None,
        kernels=max(1, kernels),
    )
    frame = _raw_features(settings, start_time, end_time)
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
            "contract": contract,
            "rows": len(frame),
            "columns": [str(column) for column in frame.columns],
            "files": files,
        }
        (building / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if target.exists():
            if not force:
                return target
            backup = root / f"{store_id}.replaced"
            if backup.exists():
                raise FileExistsError(f"feature-store backup already exists: {backup}")
            os.replace(target, backup)
        os.replace(building, target)
    finally:
        if building.exists():
            shutil.rmtree(building, ignore_errors=True)
    return target


def load_feature_store(path: Path, start_time: str, end_time: str) -> pd.DataFrame:
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"feature-store manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != FEATURE_STORE_SCHEMA:
        raise ValueError(f"unsupported feature-store schema: {manifest.get('schemaVersion')}")
    frames: list[pd.DataFrame] = []
    for entry in manifest.get("files", []):
        file_path = path / str(entry["name"])
        if not file_path.is_file() or sha256_file(file_path) != entry.get("sha256"):
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
