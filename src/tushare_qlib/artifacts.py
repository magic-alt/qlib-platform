from __future__ import annotations

import json
import hashlib
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

import pandas as pd


ARTIFACT_SCHEMA_VERSION = "2.0"


class ArtifactType(str, Enum):
    MODEL_SCORE = "MODEL_SCORE"
    MODEL_TOPK = "MODEL_TOPK"
    STRATEGY_DECISION = "STRATEGY_DECISION"
    TARGET_PORTFOLIO = "TARGET_PORTFOLIO"
    ORDER_INTENT = "ORDER_INTENT"
    BROKER_ORDER = "BROKER_ORDER"
    FILL = "FILL"


class PromotionStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    PROMOTED = "PROMOTED"
    REJECTED = "REJECTED"


class ArtifactContractError(ValueError):
    pass


_BASE_METADATA_COLUMNS = {
    "artifact_type",
    "schema_version",
    "promotion_status",
    "run_id",
    "model_id",
    "dataset_id",
    "lineage_id",
    "manifest_path",
    "payload_sha256",
}
_POLICY_METADATA_COLUMNS = {"portfolio_policy_sha256"}
_METADATA_COLUMNS = _BASE_METADATA_COLUMNS | _POLICY_METADATA_COLUMNS


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_manifest_portfolio_policy(manifest: Mapping[str, Any]) -> tuple[Mapping[str, Any], str]:
    canonical = manifest.get("canonicalConfig")
    if not isinstance(canonical, Mapping):
        raise ArtifactContractError("manifest canonical config is missing")
    portfolio = canonical.get("portfolio")
    required = {
        "top_n",
        "min_score",
        "weighting",
        "max_position",
        "max_exposure",
        "max_group_exposure",
        "max_turnover",
        "min_position",
        "volatility_floor",
    }
    if not isinstance(portfolio, Mapping) or not required.issubset(portfolio):
        missing = required - set(portfolio) if isinstance(portfolio, Mapping) else required
        raise ArtifactContractError(f"manifest canonical portfolio config is incomplete: {sorted(missing)}")
    calculated = _sha256_json(dict(portfolio))
    recorded = manifest.get("portfolioPolicySha256")
    if not isinstance(recorded, str) or not recorded:
        raise ArtifactContractError("manifest portfolio policy hash is missing")
    if recorded != calculated:
        raise ArtifactContractError("manifest portfolio policy hash does not match canonical config")
    return portfolio, calculated


def _payload_sha256(frame: pd.DataFrame) -> str:
    columns = sorted(column for column in frame.columns if column not in _METADATA_COLUMNS)
    payload: list[dict[str, object]] = []
    for row in frame[columns].to_dict(orient="records"):
        normalized: dict[str, object] = {}
        for key, value in row.items():
            if pd.isna(value):
                normalized[key] = None
            elif isinstance(value, pd.Timestamp):
                normalized[key] = value.isoformat()
            elif hasattr(value, "item"):
                normalized[key] = value.item()
            else:
                normalized[key] = value
        payload.append(normalized)
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def stamp_artifact(
    frame: pd.DataFrame,
    artifact_type: ArtifactType,
    *,
    promotion_status: PromotionStatus,
    run_id: str,
    model_id: str,
    dataset_id: str,
    lineage_id: str,
    manifest_path: str | Path,
    portfolio_policy_sha256: str | None = None,
) -> pd.DataFrame:
    if frame.empty:
        raise ArtifactContractError(f"cannot publish an empty {artifact_type.value} artifact")
    result = frame.copy()
    metadata = {
        "artifact_type": artifact_type.value,
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "promotion_status": promotion_status.value,
        "run_id": run_id,
        "model_id": model_id,
        "dataset_id": dataset_id,
        "lineage_id": lineage_id,
        "manifest_path": str(Path(manifest_path).expanduser().resolve()),
        "payload_sha256": _payload_sha256(result),
    }
    if artifact_type is ArtifactType.TARGET_PORTFOLIO:
        if not portfolio_policy_sha256:
            raise ArtifactContractError("TARGET_PORTFOLIO requires a portfolio policy hash")
        metadata["portfolio_policy_sha256"] = portfolio_policy_sha256
    for column, value in metadata.items():
        result[column] = value
    return result


def _single_value(frame: pd.DataFrame, column: str) -> str:
    values = frame[column].dropna().astype(str).unique()
    if len(values) != 1 or not values[0].strip():
        raise ArtifactContractError(f"artifact metadata {column} must contain exactly one non-empty value")
    return str(values[0])


def load_artifact_manifest(metadata: Mapping[str, str]) -> Mapping[str, Any]:
    manifest_path = Path(metadata["manifest_path"])
    if not manifest_path.is_file():
        raise ArtifactContractError(f"artifact manifest is missing: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactContractError(f"artifact manifest is unreadable: {manifest_path}") from exc
    if not isinstance(manifest, Mapping):
        raise ArtifactContractError("artifact manifest must be a JSON object")
    return manifest


def validate_artifact(
    frame: pd.DataFrame,
    expected_type: ArtifactType,
    *,
    require_promoted: bool = True,
) -> dict[str, str]:
    required_metadata = set(_BASE_METADATA_COLUMNS)
    if expected_type is ArtifactType.TARGET_PORTFOLIO:
        required_metadata.update(_POLICY_METADATA_COLUMNS)
    missing = required_metadata - set(frame.columns)
    if missing:
        raise ArtifactContractError(
            f"legacy or incomplete artifact is not executable; missing metadata: {sorted(missing)}"
        )
    metadata = {column: _single_value(frame, column) for column in sorted(required_metadata)}
    if metadata["schema_version"] != ARTIFACT_SCHEMA_VERSION:
        raise ArtifactContractError(
            f"unsupported artifact schema {metadata['schema_version']!r}; expected {ARTIFACT_SCHEMA_VERSION}"
        )
    if metadata["artifact_type"] != expected_type.value:
        raise ArtifactContractError(
            f"artifact type {metadata['artifact_type']} cannot be used as {expected_type.value}"
        )
    if require_promoted and metadata["promotion_status"] != PromotionStatus.PROMOTED.value:
        raise ArtifactContractError("only PROMOTED model artifacts may enter the execution path")
    if metadata["payload_sha256"] != _payload_sha256(frame):
        raise ArtifactContractError("artifact payload checksum mismatch")

    manifest = load_artifact_manifest(metadata)
    _, portfolio_policy_sha256 = validate_manifest_portfolio_policy(manifest)
    promotion = manifest.get("promotion")
    lineage = manifest.get("lineage")
    dataset = manifest.get("dataset")
    if not isinstance(promotion, Mapping) or promotion.get("status") != PromotionStatus.PROMOTED.value:
        raise ArtifactContractError("manifest does not record a PROMOTED model release")
    if not isinstance(lineage, Mapping) or not bool(lineage.get("complete")):
        raise ArtifactContractError("manifest lineage is missing or incomplete")
    expected = {
        "run_id": str(manifest.get("externalRunId", "")),
        "model_id": str(manifest.get("model", {}).get("fingerprint", ""))
        if isinstance(manifest.get("model"), Mapping)
        else "",
        "dataset_id": str(dataset.get("fingerprint", "")) if isinstance(dataset, Mapping) else "",
        "lineage_id": str(lineage.get("lineageId", "")),
    }
    for key, manifest_value in expected.items():
        if not manifest_value or metadata[key] != manifest_value:
            raise ArtifactContractError(f"artifact {key} does not match its manifest")
    if expected_type is ArtifactType.TARGET_PORTFOLIO:
        if metadata["portfolio_policy_sha256"] != portfolio_policy_sha256:
            raise ArtifactContractError("TARGET_PORTFOLIO policy hash does not match its release manifest")
    return metadata
