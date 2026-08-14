from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "2.0"
IMPORT_TYPE = "QLIB_RESEARCH_BUNDLE"


class ResearchArtifactType(str, Enum):
    MODEL_RELEASE = "MODEL_RELEASE"
    STRATEGY_POLICY = "STRATEGY_POLICY"
    SIGNAL_SNAPSHOT = "SIGNAL_SNAPSHOT"
    TARGET_PORTFOLIO = "TARGET_PORTFOLIO"
    VALIDATION_RESULT = "VALIDATION_RESULT"


class ResearchPromotionStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    RESEARCH_REVIEW = "RESEARCH_REVIEW"
    RESEARCH_PROMOTED = "RESEARCH_PROMOTED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class ResearchBundleContext:
    external_run_id: str
    run_kind: str
    data_release_id: str
    git_commit: str
    container_digest: str
    as_of_time: str
    signal_date: str
    trade_date: str
    timezone: str = "Asia/Shanghai"
    currency: str = "CNY"
    universe_release_id: str | None = None
    name: str | None = None


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _artifact_id(identity: Mapping[str, Any]) -> str:
    return "art_" + hashlib.sha256(_canonical_bytes(identity)).hexdigest()


def _write_payload(root: Path, artifact_id: str, payload: object) -> tuple[Path, str]:
    raw = _canonical_bytes(payload)
    digest = hashlib.sha256(raw).hexdigest()
    path = root / "payloads" / f"{artifact_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path, digest


def _target_payload(targets: Sequence[Mapping[str, Any]]) -> dict[str, object]:
    normalized: list[dict[str, object]] = []
    seen: set[str] = set()
    gross = 0.0
    for target in targets:
        instrument = str(target.get("instrument") or "").strip().upper()
        if len(instrument) != 8 or instrument[:2] not in {"SH", "SZ", "BJ"} or not instrument[2:].isdigit():
            raise ValueError(f"Invalid target instrument: {instrument}")
        if instrument in seen:
            raise ValueError(f"Duplicate target instrument: {instrument}")
        seen.add(instrument)
        weight = float(target.get("targetWeight", target.get("target_weight", 0.0)))
        if weight < 0 or weight > 1:
            raise ValueError(f"Invalid target weight: {instrument}")
        gross += weight
        normalized.append({"instrument": instrument, "targetWeight": weight, "score": target.get("score")})
    if not normalized or gross > 1.000001:
        raise ValueError("Target portfolio must be non-empty with gross exposure no greater than 1")
    return {"targets": sorted(normalized, key=lambda item: str(item["instrument"]))}


def export_research_bundle(
    output_dir: str | Path,
    *,
    context: ResearchBundleContext,
    promotion_status: ResearchPromotionStatus,
    model: Mapping[str, Any],
    strategy_policy: Mapping[str, Any],
    signals: Sequence[Mapping[str, Any]],
    targets: Sequence[Mapping[str, Any]],
    validation: Mapping[str, Any],
) -> Path:
    if not context.data_release_id.startswith("ds_") or len(context.data_release_id) != 67:
        raise ValueError("data_release_id must be a content-addressed DataRelease ID")
    if context.trade_date <= context.signal_date:
        raise ValueError("trade_date must be after signal_date")
    for name, value in {
        "external_run_id": context.external_run_id,
        "run_kind": context.run_kind,
        "git_commit": context.git_commit,
        "container_digest": context.container_digest,
        "as_of_time": context.as_of_time,
    }.items():
        if not value.strip():
            raise ValueError(f"{name} is required")

    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict[str, Any]] = []
    uploads: dict[str, str] = {}

    def publish(
        artifact_type: ResearchArtifactType,
        payload: object,
        *,
        parents: Sequence[str],
        model_release_id: str | None,
        strategy_policy_id: str | None,
        dated: bool = False,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        payload_sha = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
        identity = {
            "artifactType": artifact_type.value,
            "promotionStatus": promotion_status.value,
            "dataReleaseId": context.data_release_id,
            "payloadSha256": payload_sha,
            "parentArtifactIds": list(parents),
            "modelReleaseId": model_release_id,
            "strategyPolicyId": strategy_policy_id,
        }
        artifact_id = _artifact_id(identity)
        local_path, written_sha = _write_payload(root, artifact_id, payload)
        if written_sha != payload_sha:
            raise RuntimeError("Canonical artifact payload changed while writing")
        object_key = f"qlib/{context.external_run_id}/{artifact_id}.json"
        uploads[object_key] = str(local_path)
        artifact = {
            "schemaVersion": SCHEMA_VERSION,
            "artifactId": artifact_id,
            "artifactType": artifact_type.value,
            "promotionStatus": promotion_status.value,
            "dataReleaseId": context.data_release_id,
            "universeReleaseId": context.universe_release_id,
            "modelReleaseId": model_release_id,
            "strategyPolicyId": strategy_policy_id,
            "gitCommit": context.git_commit,
            "containerDigest": context.container_digest,
            "asOfTime": context.as_of_time,
            "signalDate": context.signal_date if dated else None,
            "tradeDate": context.trade_date if dated else None,
            "timezone": context.timezone,
            "currency": context.currency,
            "payloadSha256": payload_sha,
            "parentArtifactIds": list(parents),
            "payloadRef": {
                "objectKey": object_key,
                "sha256": payload_sha,
                "mediaType": "application/json",
                "rows": len(payload)
                if isinstance(payload, Sequence) and not isinstance(payload, str)
                else None,
            },
            "metadata": dict(metadata or {}),
        }
        artifacts.append(artifact)
        return artifact_id

    model_id = publish(
        ResearchArtifactType.MODEL_RELEASE,
        dict(model),
        parents=[],
        model_release_id=None,
        strategy_policy_id=None,
    )
    artifacts[-1]["modelReleaseId"] = model_id
    policy_id = publish(
        ResearchArtifactType.STRATEGY_POLICY,
        dict(strategy_policy),
        parents=[model_id],
        model_release_id=model_id,
        strategy_policy_id=None,
    )
    artifacts[-1]["strategyPolicyId"] = policy_id
    signal_id = publish(
        ResearchArtifactType.SIGNAL_SNAPSHOT,
        {"signals": [dict(item) for item in signals]},
        parents=[model_id, policy_id],
        model_release_id=model_id,
        strategy_policy_id=policy_id,
        dated=True,
    )
    target_payload = _target_payload(targets)
    canonical_targets = target_payload["targets"]
    targets_sha = hashlib.sha256(_canonical_bytes(canonical_targets)).hexdigest()
    target_id = publish(
        ResearchArtifactType.TARGET_PORTFOLIO,
        target_payload,
        parents=[signal_id, policy_id],
        model_release_id=model_id,
        strategy_policy_id=policy_id,
        dated=True,
        metadata={"targetsSha256": targets_sha},
    )
    validation_id = publish(
        ResearchArtifactType.VALIDATION_RESULT,
        dict(validation),
        parents=[target_id],
        model_release_id=model_id,
        strategy_policy_id=policy_id,
    )
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "importType": IMPORT_TYPE,
        "externalRunId": context.external_run_id,
        "runKind": context.run_kind,
        "name": context.name,
        "rootArtifactIds": [validation_id],
        "artifacts": artifacts,
    }
    path = root / "qlib_research_bundle.v2.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    path.with_name("qlib_research_bundle.v2.uploads.json").write_text(
        json.dumps({"schemaVersion": SCHEMA_VERSION, "uploads": uploads}, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return path
