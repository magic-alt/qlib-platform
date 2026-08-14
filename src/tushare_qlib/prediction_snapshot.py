from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .lineage import sha256_json
from .research_experiment import ResearchExperimentSpec
from .store import sha256_file


PREDICTION_SNAPSHOT_SCHEMA = "prediction_snapshot_v1"
PREDICTION_SNAPSHOT_TYPE = "PREDICTION_SNAPSHOT"


@dataclass(frozen=True)
class PredictionSnapshotSpec:
    data_release_id: str
    alpha_pack_id: str
    feature_snapshot_id: str
    label_spec_id: str
    split_spec_id: str
    model_id: str
    model_profile_id: str
    fold_id: str

    @classmethod
    def from_experiment(
        cls,
        experiment: ResearchExperimentSpec,
        *,
        feature_snapshot_id: str,
        model_id: str,
        fold_id: str,
    ) -> "PredictionSnapshotSpec":
        return cls(
            data_release_id=experiment.data_release_id,
            alpha_pack_id=experiment.alpha_pack_id,
            feature_snapshot_id=feature_snapshot_id,
            label_spec_id=experiment.label_spec_id,
            split_spec_id=experiment.split_sha256,
            model_id=model_id,
            model_profile_id=experiment.model_profile_id,
            fold_id=fold_id,
        )


def prediction_snapshot_path(payload_path: str | Path) -> Path:
    return Path(payload_path).with_suffix(".snapshot.json")


def _normalize_frame(
    predictions: pd.Series | pd.DataFrame,
    labels: pd.Series | pd.DataFrame | None,
) -> pd.DataFrame:
    frame = predictions.to_frame("score") if isinstance(predictions, pd.Series) else predictions.copy()
    if not isinstance(frame.index, pd.MultiIndex) or frame.index.names != ["datetime", "instrument"]:
        raise ValueError("prediction snapshot requires a datetime/instrument MultiIndex")
    if "score" not in frame:
        if len(frame.columns) != 1:
            raise ValueError("prediction snapshot requires one score column")
        frame = frame.rename(columns={frame.columns[0]: "score"})
    result = frame[["score"]].copy()
    result["score"] = pd.to_numeric(result["score"], errors="raise")
    if result.empty or not np.isfinite(result["score"].to_numpy(dtype=float)).all():
        raise ValueError("prediction snapshot scores must be non-empty and finite")
    if labels is not None:
        label_frame = labels.to_frame("label") if isinstance(labels, pd.Series) else labels.copy()
        if not isinstance(label_frame.index, pd.MultiIndex) or label_frame.index.names != [
            "datetime",
            "instrument",
        ]:
            raise ValueError("prediction snapshot labels require a datetime/instrument MultiIndex")
        if "label" not in label_frame:
            if len(label_frame.columns) != 1:
                raise ValueError("prediction snapshot requires one label column")
            label_frame = label_frame.rename(columns={label_frame.columns[0]: "label"})
        result["label"] = pd.to_numeric(label_frame["label"].reindex(result.index), errors="coerce")
    result = result.sort_index()
    if result.index.has_duplicates:
        raise ValueError("prediction snapshot contains duplicate datetime/instrument rows")
    if (result.index.get_level_values("instrument").astype(str).str.strip() == "").any():
        raise ValueError("prediction snapshot contains an empty instrument")
    return result


def _identity(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": manifest["schemaVersion"],
        "artifactType": manifest["artifactType"],
        "contract": manifest["contract"],
        "payloadSha256": manifest["payload"]["sha256"],
        "rows": manifest["payload"]["rows"],
        "columns": manifest["payload"]["columns"],
        "coverage": manifest["payload"]["coverage"],
    }


def write_prediction_snapshot(
    payload_path: str | Path,
    predictions: pd.Series | pd.DataFrame,
    *,
    spec: PredictionSnapshotSpec,
    labels: pd.Series | pd.DataFrame | None = None,
) -> dict[str, Any]:
    contract = asdict(spec)
    missing = sorted(key for key, value in contract.items() if not str(value).strip())
    if missing:
        raise ValueError(f"prediction snapshot contract has empty fields: {missing}")
    target = Path(payload_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    frame = _normalize_frame(predictions, labels)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        frame.to_parquet(temporary)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    dates = pd.DatetimeIndex(frame.index.get_level_values("datetime")).normalize()
    manifest: dict[str, Any] = {
        "schemaVersion": PREDICTION_SNAPSHOT_SCHEMA,
        "artifactType": PREDICTION_SNAPSHOT_TYPE,
        "contract": contract,
        "payload": {
            "path": target.name,
            "sha256": sha256_file(target),
            "rows": len(frame),
            "columns": list(frame.columns),
            "coverage": {
                "startDate": str(dates.min().date()),
                "endDate": str(dates.max().date()),
            },
            "instrumentCount": int(frame.index.get_level_values("instrument").nunique()),
        },
    }
    manifest["snapshotId"] = "ps_" + sha256_json(_identity(manifest))
    sidecar = prediction_snapshot_path(target)
    temporary_manifest = sidecar.with_name(f".{sidecar.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
        )
        os.replace(temporary_manifest, sidecar)
    finally:
        if temporary_manifest.exists():
            temporary_manifest.unlink()
    return manifest


def load_prediction_snapshot(path: str | Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    sidecar = source if source.suffix == ".json" else prediction_snapshot_path(source)
    if not sidecar.is_file():
        raise FileNotFoundError(f"prediction snapshot manifest not found: {sidecar}")
    manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("prediction snapshot manifest must be an object")
    if manifest.get("schemaVersion") != PREDICTION_SNAPSHOT_SCHEMA:
        raise ValueError(f"unsupported prediction snapshot schema: {manifest.get('schemaVersion')}")
    if manifest.get("artifactType") != PREDICTION_SNAPSHOT_TYPE:
        raise ValueError("prediction snapshot artifact type is invalid")
    expected_id = "ps_" + sha256_json(_identity(manifest))
    if manifest.get("snapshotId") != expected_id:
        raise ValueError("prediction snapshot identity mismatch")
    payload = manifest.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("prediction snapshot payload metadata is missing")
    payload_path = (sidecar.parent / str(payload.get("path") or "")).resolve()
    if payload_path.parent != sidecar.parent or not payload_path.is_file():
        raise ValueError("prediction snapshot payload path is invalid")
    if sha256_file(payload_path) != payload.get("sha256"):
        raise ValueError("prediction snapshot payload checksum mismatch")
    frame = _normalize_frame(pd.read_parquet(payload_path), None)
    expected_columns = [str(value) for value in payload.get("columns", [])]
    if expected_columns == ["score", "label"]:
        raw = pd.read_parquet(payload_path)
        frame = _normalize_frame(raw[["score"]], raw[["label"]])
    if len(frame) != int(payload.get("rows", -1)) or list(frame.columns) != expected_columns:
        raise ValueError("prediction snapshot payload schema or row count mismatch")
    dates = pd.DatetimeIndex(frame.index.get_level_values("datetime")).normalize()
    coverage = payload.get("coverage", {})
    actual_coverage = {"startDate": str(dates.min().date()), "endDate": str(dates.max().date())}
    if coverage != actual_coverage:
        raise ValueError("prediction snapshot coverage mismatch")
    contract = manifest.get("contract")
    if not isinstance(contract, Mapping) or any(not str(value).strip() for value in contract.values()):
        raise ValueError("prediction snapshot contract is incomplete")
    return frame, manifest
