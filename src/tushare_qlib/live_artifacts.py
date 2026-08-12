from __future__ import annotations

import hashlib
import json
import pandas as pd

from .artifact_resolver import ArtifactResolver
from .artifacts import ArtifactType


LIVE_ARTIFACT_SCHEMA_VERSION = "3.0"
_LIVE_METADATA = {
    "artifact_type",
    "schema_version",
    "producer",
    "deployment_id",
    "dataset_sha256",
    "signal_id",
    "manifest_uri",
    "manifest_sha256",
    "payload_sha256",
}


def payload_sha256(frame: pd.DataFrame) -> str:
    columns = sorted(column for column in frame.columns if column not in _LIVE_METADATA)
    values = frame.loc[:, columns].copy()
    if not values.empty:
        values = values.sort_values(columns, kind="stable", na_position="last").reset_index(drop=True)
    records = []
    for row in values.to_dict(orient="records"):
        records.append(
            {
                key: None
                if pd.isna(value)
                else value.item()
                if hasattr(value, "item")
                else value.isoformat()
                if isinstance(value, pd.Timestamp)
                else value
                for key, value in row.items()
            }
        )
    encoded = json.dumps(records, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def stamp_live_artifact(
    frame: pd.DataFrame,
    artifact_type: ArtifactType,
    *,
    deployment_id: str,
    dataset_sha256: str,
    signal_id: str,
    manifest_uri: str,
    manifest_sha256: str,
) -> pd.DataFrame:
    if frame.empty:
        raise ValueError(f"cannot publish empty live artifact: {artifact_type.value}")
    result = frame.copy()
    metadata = {
        "artifact_type": artifact_type.value,
        "schema_version": LIVE_ARTIFACT_SCHEMA_VERSION,
        "producer": "LIVE_INFERENCE" if artifact_type is ArtifactType.MODEL_SCORE else "QLIB_PLATFORM",
        "deployment_id": deployment_id,
        "dataset_sha256": dataset_sha256,
        "signal_id": signal_id,
        "manifest_uri": manifest_uri,
        "manifest_sha256": manifest_sha256,
        "payload_sha256": payload_sha256(result),
    }
    for column, value in metadata.items():
        result[column] = value
    return result


def _single(frame: pd.DataFrame, column: str) -> str:
    values = frame[column].dropna().astype(str).unique()
    if len(values) != 1 or not values[0]:
        raise ValueError(f"live artifact metadata {column} must have one value")
    return str(values[0])


def validate_live_artifact(
    frame: pd.DataFrame,
    expected_type: ArtifactType,
    *,
    resolver: ArtifactResolver,
    expected_deployment_id: str | None = None,
) -> dict[str, str]:
    required = _LIVE_METADATA | {"signal_date", "trade_date"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"live artifact is incomplete; missing={sorted(missing)}")
    metadata = {name: _single(frame, name) for name in _LIVE_METADATA}
    if metadata["schema_version"] != LIVE_ARTIFACT_SCHEMA_VERSION:
        raise ValueError("only schema 3.0 live artifacts may enter production execution")
    if metadata["artifact_type"] != expected_type.value:
        raise ValueError(f"artifact type cannot be used as {expected_type.value}")
    if expected_type is ArtifactType.MODEL_SCORE and metadata["producer"] != "LIVE_INFERENCE":
        raise ValueError("production MODEL_SCORE must be produced by LIVE_INFERENCE")
    if expected_deployment_id and metadata["deployment_id"] != expected_deployment_id:
        raise ValueError("live artifact deployment does not match expected deployment")
    if payload_sha256(frame) != metadata["payload_sha256"]:
        raise ValueError("live artifact payload checksum mismatch")
    attestation_path = resolver.resolve(
        metadata["manifest_uri"], expected_sha256=metadata["manifest_sha256"]
    )
    attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
    payloads = attestation.get("artifactPayloads", {})
    attested_payload = (
        payloads.get(expected_type.value)
        if isinstance(payloads, dict)
        else None
    )
    expected = {
        "deployment_id": str(attestation.get("deploymentId", "")),
        "dataset_sha256": str(attestation.get("datasetSha256", "")),
        "signal_id": str(attestation.get("signalId", "")),
        "payload_sha256": str(attested_payload or attestation.get("signalSha256", "")),
    }
    for key, value in expected.items():
        if not value or metadata[key] != value:
            raise ValueError(f"live artifact {key} does not match signal attestation")
    return metadata
