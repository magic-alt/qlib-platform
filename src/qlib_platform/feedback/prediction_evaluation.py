from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from qlib_platform.lineage import sha256_json
from qlib_platform.artifacts.prediction_snapshot import load_prediction_snapshot
from qlib_platform.data.store import sha256_file
from qlib_platform.feedback.realized_labels import load_realized_label_snapshot


PREDICTION_EVALUATION_SCHEMA = "prediction_evaluation_snapshot_v1"
PREDICTION_EVALUATION_TYPE = "PREDICTION_EVALUATION_SNAPSHOT"


def prediction_evaluation_manifest_path(payload_path: str | Path) -> Path:
    return Path(payload_path).with_suffix(".evaluation.json")


def _identity(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": manifest["schemaVersion"],
        "artifactType": manifest["artifactType"],
        "contract": manifest["contract"],
        "parameters": manifest["parameters"],
        "payloadSha256": manifest["payload"]["sha256"],
        "summary": manifest["summary"],
        "decision": manifest["decision"],
    }


def _daily_metrics(frame: pd.DataFrame, *, topk: int, min_cross_section: int) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for date, group in frame.groupby(level="datetime", sort=True):
        pair = group[["score", "label"]].dropna()
        count = len(pair)
        ic = pair["score"].corr(pair["label"], method="pearson") if count >= 2 else np.nan
        rank_ic = pair["score"].corr(pair["label"], method="spearman") if count >= 2 else np.nan
        tail = min(topk, count // 2)
        ordered = pair.sort_values("score", ascending=False)
        spread = (
            float(ordered.head(tail)["label"].mean() - ordered.tail(tail)["label"].mean())
            if tail > 0
            else np.nan
        )
        rows.append(
            {
                "datetime": pd.Timestamp(date).normalize(),
                "sample_count": count,
                "ic": ic,
                "rank_ic": rank_ic,
                "top_bottom_spread": spread,
                "cross_section_passed": count >= min_cross_section,
            }
        )
    return pd.DataFrame(rows).set_index("datetime")


def _summary(daily: pd.DataFrame) -> dict[str, Any]:
    def finite_mean(column: str) -> float | None:
        values = pd.to_numeric(daily[column], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        return float(values.mean()) if len(values) else None

    rank = pd.to_numeric(daily["rank_ic"], errors="coerce").dropna()
    rank_std = float(rank.std(ddof=0)) if len(rank) else float("nan")
    rank_mean = float(rank.mean()) if len(rank) else float("nan")
    return {
        "dateCount": len(daily),
        "sampleCount": int(daily["sample_count"].sum()),
        "meanIc": finite_mean("ic"),
        "meanRankIc": finite_mean("rank_ic"),
        "rankIcIr": rank_mean / rank_std if np.isfinite(rank_std) and rank_std > 0 else None,
        "meanTopBottomSpread": finite_mean("top_bottom_spread"),
    }


def evaluate_prediction_snapshot(
    output_path: str | Path,
    *,
    prediction_snapshot: str | Path,
    realized_label_snapshot: str | Path,
    topk: int = 50,
    min_cross_section: int = 20,
    rolling_window: int = 20,
) -> dict[str, Any]:
    if topk <= 0 or min_cross_section < 2 or rolling_window <= 0:
        raise ValueError("topk and rolling_window must be positive; min_cross_section must be at least 2")
    predictions, prediction_manifest = load_prediction_snapshot(prediction_snapshot)
    realized, realized_manifest = load_realized_label_snapshot(realized_label_snapshot)
    prediction_contract = prediction_manifest["contract"]
    realized_contract = realized_manifest["contract"]
    for key in ("data_release_id", "label_spec_id"):
        if prediction_contract.get(key) != realized_contract.get(key):
            raise ValueError(f"prediction and realized label {key} binding mismatch")
    missing = predictions.index.difference(realized.index)
    if len(missing):
        raise ValueError(f"realized labels do not cover prediction keys: {len(missing)} missing")
    frame = predictions[["score"]].join(realized[["label"]], how="left", validate="one_to_one")
    if frame["label"].isna().any():
        raise ValueError("realized label join produced missing values")
    daily = _daily_metrics(frame, topk=topk, min_cross_section=min_cross_section)
    daily["rolling_rank_ic"] = daily["rank_ic"].rolling(rolling_window, min_periods=1).mean()
    reasons: list[str] = []
    if not bool(daily["cross_section_passed"].all()):
        reasons.append("CROSS_SECTION_TOO_SMALL")
    if not np.isfinite(daily[["ic", "rank_ic", "top_bottom_spread"]].to_numpy(dtype=float)).all():
        reasons.append("NON_FINITE_EVALUATION_METRIC")
    summary = _summary(daily)
    decision = {"status": "PASS" if not reasons else "REJECTED", "reasons": reasons}

    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        daily.to_parquet(temporary)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    manifest: dict[str, Any] = {
        "schemaVersion": PREDICTION_EVALUATION_SCHEMA,
        "artifactType": PREDICTION_EVALUATION_TYPE,
        "contract": {
            "dataReleaseId": prediction_contract["data_release_id"],
            "labelSpecId": prediction_contract["label_spec_id"],
            "predictionSnapshotId": prediction_manifest["snapshotId"],
            "realizedLabelSnapshotId": realized_manifest["snapshotId"],
        },
        "parameters": {
            "topk": topk,
            "minCrossSection": min_cross_section,
            "rollingWindow": rolling_window,
        },
        "payload": {
            "path": target.name,
            "sha256": sha256_file(target),
            "rows": len(daily),
            "columns": list(daily.columns),
        },
        "summary": summary,
        "decision": decision,
    }
    manifest["evaluationId"] = "pes_" + sha256_json(_identity(manifest))
    sidecar = prediction_evaluation_manifest_path(target)
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


def load_prediction_evaluation(path: str | Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    sidecar = source if source.suffix == ".json" else prediction_evaluation_manifest_path(source)
    if not sidecar.is_file():
        raise FileNotFoundError(f"prediction evaluation manifest not found: {sidecar}")
    manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("prediction evaluation manifest must be an object")
    if manifest.get("schemaVersion") != PREDICTION_EVALUATION_SCHEMA:
        raise ValueError(f"unsupported prediction evaluation schema: {manifest.get('schemaVersion')}")
    if manifest.get("artifactType") != PREDICTION_EVALUATION_TYPE:
        raise ValueError("prediction evaluation artifact type is invalid")
    if manifest.get("evaluationId") != "pes_" + sha256_json(_identity(manifest)):
        raise ValueError("prediction evaluation identity mismatch")
    payload = manifest.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("prediction evaluation payload metadata is missing")
    payload_path = (sidecar.parent / str(payload.get("path") or "")).resolve()
    if payload_path.parent != sidecar.parent or not payload_path.is_file():
        raise ValueError("prediction evaluation payload path is invalid")
    if sha256_file(payload_path) != payload.get("sha256"):
        raise ValueError("prediction evaluation payload checksum mismatch")
    frame = pd.read_parquet(payload_path)
    if len(frame) != int(payload.get("rows", -1)) or list(frame.columns) != payload.get("columns"):
        raise ValueError("prediction evaluation payload schema or row count mismatch")
    return frame, manifest
