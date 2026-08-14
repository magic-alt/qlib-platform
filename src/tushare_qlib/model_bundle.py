from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

import numpy as np
import pandas as pd
import yaml

from .artifact_resolver import ArtifactResolver, sha256_path
from .models.registry import get_model_adapter
from .settings import Settings


MODEL_BUNDLE_SCHEMA_VERSION = "1.0"


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def deployment_root(settings: Settings) -> Path:
    return settings.paths.models / "deployments"


def _normalizer_state(dataset: Any, feature_columns: Sequence[str]) -> tuple[np.ndarray, np.ndarray]:
    for processor in dataset.handler.infer_processors:
        if processor.__class__.__name__ == "RobustZScoreNorm":
            means = np.asarray(processor.mean_train, dtype=np.float64)
            scales = np.asarray(processor.std_train, dtype=np.float64)
            if len(means) != len(feature_columns) or len(scales) != len(feature_columns):
                raise ValueError("fitted normalizer width does not match feature schema")
            return means, scales
    raise ValueError("approved processor recipe has no fitted RobustZScoreNorm")


def _save_model(model: Any, family: str, root: Path) -> str:
    return get_model_adapter(family).save(model, root)


def _model_scores(model: Any, family: str, features: pd.DataFrame) -> np.ndarray:
    return get_model_adapter(family).scores(model, features)


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
    """Write an immutable, checksummed and cross-machine model bundle."""

    feature_columns = [str(column) for column in dataset.handler.get_cols(col_set="feature")]
    means, scales = _normalizer_state(dataset, feature_columns)
    from qlib.data.dataset.handler import DataHandlerLP

    parity_features = dataset.prepare("test", col_set="feature", data_key=DataHandlerLP.DK_I)
    if parity_features.empty:
        raise ValueError("production refit produced no parity inference rows")
    parity_features = parity_features.loc[:, feature_columns].sort_index()
    parity_scores = pd.DataFrame(
        {"score": _model_scores(model, family, parity_features)}, index=parity_features.index
    )

    roots = deployment_root(settings)
    roots.mkdir(parents=True, exist_ok=True)
    building = Path(tempfile.mkdtemp(prefix=".bundle-building-", dir=roots))
    try:
        model_name = _save_model(model, family, building)
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
        adapter = get_model_adapter(family)
        adapter_module = sys.modules[adapter.__class__.__module__]
        adapter_source = Path(str(adapter_module.__file__)).resolve()
        package_root = Path(__file__).resolve().parent
        adapter_relative = adapter_source.relative_to(package_root).as_posix()
        implementation_checksums = {
            name: sha256_path(Path(__file__).with_name(name))
            for name in ("custom_handler.py", "processors.py")
        }
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
            "featureSchemaSha256": payload_checksums["feature_schema.json"],
            "preprocessingSha256": payload_checksums["preprocessing.npz"],
            "canonicalConfigSha256": payload_checksums["canonical_config.yaml"],
            "modelBinarySha256": payload_checksums[model_name],
            "featureStore": dict(feature_store or {}),
            "lineage": dict(lineage),
            "runtime": runtime_manifest,
            "implementationSha256": implementation_checksums,
            "randomSeed": seed,
            "referenceCrossSectionCount": int(parity_features.index.get_level_values("instrument").nunique()),
            "referenceScoreMean": float(parity_scores["score"].mean()),
            "referenceScoreStd": float(parity_scores["score"].std(ddof=0)),
            "referenceScoreQuantiles": [
                float(value) for value in parity_scores["score"].quantile(np.linspace(0.0, 1.0, 11)).tolist()
            ],
            "createdAtUtc": _utc_now(),
        }
        (building / "model_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        checksums = {
            path.name: sha256_path(path)
            for path in sorted(building.iterdir())
            if path.is_file() and path.name != "checksums.json"
        }
        (building / "checksums.json").write_text(
            json.dumps(checksums, sort_keys=True, indent=2), encoding="utf-8"
        )
        target = roots / deployment_id
        if target.exists():
            verify_model_bundle(target)
            existing = json.loads((target / "model_manifest.json").read_text(encoding="utf-8"))
            comparable_existing = {key: value for key, value in existing.items() if key != "createdAtUtc"}
            comparable_new = {key: value for key, value in manifest.items() if key != "createdAtUtc"}
            if comparable_existing != comparable_new:
                raise ValueError("deterministic deployment id already exists with different metadata")
            return target / "model_manifest.json"
        os.replace(building, target)
        return target / "model_manifest.json"
    finally:
        if building.exists():
            shutil.rmtree(building, ignore_errors=True)


def verify_model_bundle(root: str | Path) -> dict[str, Any]:
    bundle = Path(root)
    manifest_path = bundle / "model_manifest.json"
    checksums_path = bundle / "checksums.json"
    if not manifest_path.is_file() or not checksums_path.is_file():
        raise ValueError(f"incomplete model bundle: {bundle}")
    checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
    for name, expected in checksums.items():
        path = bundle / str(name)
        if not path.is_file() or sha256_path(path) != expected:
            raise ValueError(f"model bundle checksum mismatch: {name}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("model bundle manifest must be an object")
    if manifest.get("schemaVersion") != MODEL_BUNDLE_SCHEMA_VERSION:
        raise ValueError(f"unsupported model bundle schema: {manifest.get('schemaVersion')}")
    if bundle.name != manifest.get("deploymentId"):
        raise ValueError("model bundle directory does not match deployment id")
    implementation = manifest.get("implementationSha256")
    if not isinstance(implementation, Mapping):
        raise ValueError("model bundle implementation checksums are missing")
    for name, expected in implementation.items():
        source = Path(__file__).resolve().parent / str(name)
        if not source.is_file() or sha256_path(source) != expected:
            raise ValueError(f"model bundle implementation mismatch: {name}")
    return cast(dict[str, Any], manifest)


@dataclass
class LoadedModelBundle:
    root: Path
    manifest: dict[str, Any]
    feature_columns: list[str]
    mean: np.ndarray
    scale: np.ndarray
    model: Any

    def predict(self, features: pd.DataFrame) -> pd.Series:
        missing = set(self.feature_columns) - set(features.columns)
        extra = set(features.columns) - set(self.feature_columns)
        if missing or extra:
            raise ValueError(
                f"live feature schema mismatch; missing={sorted(missing)}, extra={sorted(extra)}"
            )
        ordered = features.loc[:, self.feature_columns]
        family = str(self.manifest["modelFamily"])
        values = get_model_adapter(family).predict_loaded(self.model, ordered)
        return pd.Series(np.asarray(values).reshape(-1), index=ordered.index, name="score")


def load_model_bundle(
    root: str | Path, *, device: str = "cpu", verify_parity: bool = True
) -> LoadedModelBundle:
    bundle = Path(root)
    manifest = verify_model_bundle(bundle)
    schema = json.loads((bundle / "feature_schema.json").read_text(encoding="utf-8"))
    preprocessing = np.load(bundle / "preprocessing.npz")
    family = str(manifest["modelFamily"])
    parameters = json.loads((bundle / "model_parameters.json").read_text(encoding="utf-8"))
    adapter = get_model_adapter(family)
    model = adapter.load(bundle, manifest, parameters, device=device)
    loaded = LoadedModelBundle(
        root=bundle,
        manifest=manifest,
        feature_columns=[str(value) for value in schema["columns"]],
        mean=np.asarray(preprocessing["mean"], dtype=float),
        scale=np.asarray(preprocessing["scale"], dtype=float),
        model=model,
    )
    if verify_parity:
        features = pd.read_parquet(bundle / "parity_features.parquet")
        expected = pd.read_parquet(bundle / "parity_scores.parquet")["score"]
        actual = loaded.predict(features)
        tolerance = adapter.parity_tolerance
        if not np.allclose(actual.to_numpy(), expected.to_numpy(), rtol=tolerance, atol=tolerance):
            raise ValueError("model bundle parity validation failed")
    return loaded


def bundle_uri(manifest: Mapping[str, Any]) -> str:
    return ArtifactResolver.deployment_uri(str(manifest["deploymentId"]))
