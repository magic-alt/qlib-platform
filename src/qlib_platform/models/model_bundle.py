from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import yaml

from qlib_platform.artifacts.artifact_resolver import ArtifactResolver, sha256_path
from qlib_platform.models.registry import get_model_adapter
from qlib_platform.settings import Settings

MODEL_BUNDLE_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class LoadedModelBundle:
    root: Path
    manifest: dict[str, Any]
    model: Any
    feature_columns: tuple[str, ...]
    preprocessing: dict[str, Any]
    mean: np.ndarray
    scale: np.ndarray

    def predict(self, features: pd.DataFrame) -> pd.Series:
        missing = [column for column in self.feature_columns if column not in features]
        if missing:
            raise ValueError(f"live features are missing required model columns: {missing[:10]}")
        frame = features.loc[:, list(self.feature_columns)].copy()
        values = frame.to_numpy(dtype=float)
        values = (values - self.mean) / self.scale
        values = np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)
        adapter = get_model_adapter(str(self.manifest["modelFamily"]))
        score = adapter.predict_serialized(self.model, values)
        return pd.Series(np.asarray(score, dtype=float).reshape(-1), index=frame.index, name="score")


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    import hashlib

    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _normalization_state(dataset: Any, feature_columns: list[str]) -> tuple[np.ndarray, np.ndarray]:
    handler = getattr(dataset, "handler", None)
    processors = list(getattr(handler, "infer_processors", []) or [])
    for processor in processors:
        mean = getattr(processor, "mean_train", None)
        scale = getattr(processor, "std_train", None)
        if mean is not None and scale is not None:
            mean_array = np.asarray(mean, dtype=float).reshape(-1)
            scale_array = np.asarray(scale, dtype=float).reshape(-1)
            if len(mean_array) == len(feature_columns) and len(scale_array) == len(feature_columns):
                scale_array = np.where(scale_array == 0.0, 1.0, scale_array)
                return mean_array, scale_array
    return np.zeros(len(feature_columns), dtype=float), np.ones(len(feature_columns), dtype=float)


def _parity_sample(dataset: Any, feature_columns: list[str], limit: int = 512) -> pd.DataFrame:
    frame = dataset.prepare("test", col_set="feature", data_key="infer")
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(-1)
    return frame.loc[:, feature_columns].sort_index().head(limit)


def create_model_bundle(
    settings: Settings,
    *,
    model: Any,
    dataset: Any,
    family: str,
    model_parameters: Mapping[str, Any],
    canonical_config: Mapping[str, Any],
    research_run_id: str,
    refit_as_of: str,
    train_window: tuple[str, str],
    valid_window: tuple[str, str],
    dataset_id: str,
    dataset_sha256: str,
    feature_store: Mapping[str, Any] | None,
    lineage: Mapping[str, Any],
    seed: int,
    runtime: Mapping[str, Any] | None = None,
    refit_metadata: Mapping[str, Any] | None = None,
) -> Path:
    output_root = settings.paths.models / "production"
    output_root.mkdir(parents=True, exist_ok=True)
    building = Path(tempfile.mkdtemp(prefix=".building.", dir=output_root))
    try:
        feature_columns = [str(value) for value in dataset.handler.get_cols(col_set="feature")]
        means, scales = _normalization_state(dataset, feature_columns)
        parity_features = _parity_sample(dataset, feature_columns)
        parity_values = parity_features.to_numpy(dtype=float)
        parity_values = (parity_values - means) / scales
        parity_values = np.nan_to_num(parity_values, nan=0.0, posinf=0.0, neginf=0.0)
        adapter = get_model_adapter(family)
        model_name = adapter.serialize(model, building)
        parity_scores = pd.Series(
            np.asarray(adapter.predict_serialized(adapter.load_serialized(building / model_name), parity_values), dtype=float).reshape(-1),
            index=parity_features.index,
            name="score",
        )
        (building / "feature_schema.json").write_text(
            json.dumps({"columns": feature_columns}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        np.savez_compressed(building / "preprocessing.npz", mean=means, scale=scales)
        preprocessing = {
            "schemaVersion": "1.0",
            "pipeline": [
                "AshareUniverseFilter",
                "ProcessInfSingleThread",
                "RobustZScoreNorm",
                "Fillna",
            ],
            "normalizer": "RobustZScoreNorm",
            "clipOutlier": True,
            "fillValue": 0.0,
            "stateFile": "preprocessing.npz",
        }
        (building / "preprocessing.json").write_text(
            json.dumps(preprocessing, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (building / "model_parameters.json").write_text(
            json.dumps(dict(model_parameters), ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        (building / "canonical_config.yaml").write_text(
            yaml.safe_dump(dict(canonical_config), allow_unicode=True, sort_keys=True), encoding="utf-8"
        )
        parity_features.to_parquet(building / "parity_features.parquet")
        parity_scores.to_parquet(building / "parity_scores.parquet")
        payload_files = sorted(path.name for path in building.iterdir() if path.is_file())
        payload_checksums = {name: sha256_path(building / name) for name in payload_files}
        runtime_manifest = dict(runtime or {})
        adapter_module = sys.modules[adapter.__class__.__module__]
        adapter_source = Path(str(adapter_module.__file__)).resolve()
        package_root = Path(__file__).resolve().parents[1]
        implementation_sources = (
            package_root / "data" / "custom_handler.py",
            package_root / "data" / "processors.py",
        )
        implementation_checksums = {
            source.relative_to(package_root).as_posix(): sha256_path(source)
            for source in implementation_sources
        }
        adapter_relative = adapter_source.relative_to(package_root).as_posix()
        implementation_checksums[adapter_relative] = sha256_path(adapter_source)
        identity = {
            "researchRunId": research_run_id,
            "refitAsOf": refit_as_of,
            "trainWindow": list(train_window),
            "validWindow": list(valid_window),
            "datasetSha256": dataset_sha256,
            "payloadChecksums": payload_checksums,
            "runtime": runtime_manifest,
            "refit": dict(refit_metadata or {}),
            "implementationSha256": implementation_checksums,
        }
        deployment_id = _canonical_sha256(identity)[:32]
        manifest = {
            "schemaVersion": MODEL_BUNDLE_SCHEMA_VERSION,
            "deploymentId": deployment_id,
            "researchRunId": research_run_id,
            "modelFamily": family,
            "modelFile": model_name,
            "modelParametersFile": "model_parameters.json",
            "refitAsOf": refit_as_of,
            "trainStartDate": train_window[0],
            "trainEndDate": train_window[1],
            "validStartDate": valid_window[0],
            "validEndDate": valid_window[1],
            "datasetId": dataset_id,
            "datasetSha256": dataset_sha256,
            "featureStore": dict(feature_store or {}),
            "featureSchemaFile": "feature_schema.json",
            "preprocessingFile": "preprocessing.json",
            "canonicalConfigFile": "canonical_config.yaml",
            "parityFeaturesFile": "parity_features.parquet",
            "parityScoresFile": "parity_scores.parquet",
            "runtime": runtime_manifest,
            "refit": dict(refit_metadata or {}),
            "lineage": dict(lineage),
            "seed": int(seed),
            "payloadChecksums": payload_checksums,
            "implementationSha256": implementation_checksums,
            "identitySha256": _canonical_sha256(identity),
        }
        (building / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
        )
        target = output_root / deployment_id
        if target.exists():
            verify_model_bundle(target)
            shutil.rmtree(building)
            return target / "manifest.json"
        os.replace(building, target)
        verify_model_bundle(target)
        return target / "manifest.json"
    finally:
        if building.exists():
            shutil.rmtree(building, ignore_errors=True)


def verify_model_bundle(root: str | Path) -> dict[str, Any]:
    bundle = Path(root).expanduser().resolve()
    manifest_path = bundle / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"model bundle manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checksums = manifest.get("payloadChecksums")
    if not isinstance(checksums, Mapping):
        raise ValueError("model bundle manifest is missing payloadChecksums")
    for name, expected in checksums.items():
        path = bundle / str(name)
        if not path.is_file():
            raise FileNotFoundError(f"model bundle payload is missing: {path}")
        actual = sha256_path(path)
        if actual != expected:
            raise ValueError(f"model bundle checksum mismatch: {name}")
    return manifest


def load_model_bundle(
    root: str | Path,
    *,
    resolver: ArtifactResolver | None = None,
) -> LoadedModelBundle:
    bundle = Path(root).expanduser().resolve()
    manifest = verify_model_bundle(bundle)
    if resolver is not None:
        resolver.resolve(bundle / "manifest.json")
    family = str(manifest["modelFamily"])
    adapter = get_model_adapter(family)
    model = adapter.load_serialized(bundle / str(manifest["modelFile"]))
    feature_schema = json.loads((bundle / str(manifest["featureSchemaFile"])).read_text(encoding="utf-8"))
    preprocessing = json.loads((bundle / str(manifest["preprocessingFile"])).read_text(encoding="utf-8"))
    state = np.load(bundle / str(preprocessing["stateFile"]))
    return LoadedModelBundle(
        root=bundle,
        manifest=manifest,
        model=model,
        feature_columns=tuple(str(value) for value in feature_schema["columns"]),
        preprocessing=preprocessing,
        mean=np.asarray(state["mean"], dtype=float),
        scale=np.asarray(state["scale"], dtype=float),
    )


def bundle_uri(settings: Settings, deployment_id: str) -> Path:
    return settings.paths.models / "production" / deployment_id
