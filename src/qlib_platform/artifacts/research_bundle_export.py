from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from qlib_platform.artifacts.artifact_contract_v3 import (
    SCHEMA_VERSION as V3_SCHEMA_VERSION,
    build_artifact_identity_v3,
    canonicalize_target_instruction_v3,
    validate_portable_payload_ref,
)
from qlib_platform.artifacts.institutional_artifacts import (
    ResearchBundleContext,
    ResearchPromotionStatus,
    export_research_bundle,
)


_DATA_RELEASE_ID = re.compile(r"^ds_[0-9a-f]{64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _validated_data_release_id(value: object, *, source: str) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    if not _DATA_RELEASE_ID.fullmatch(candidate):
        raise ValueError(f"Invalid DataRelease identity from {source}: expected ds_<64 lowercase hex>")
    return candidate


def _validated_universe_release_id(value: object, *, source: str) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    if any(character.isspace() for character in candidate):
        raise ValueError(f"Invalid UniverseRelease identity from {source}: whitespace is not allowed")
    return candidate


def resolve_data_release_id(manifest: Mapping[str, Any], override: str | None) -> str:
    """Resolve exactly one canonical DataRelease without silently repairing conflicts."""

    embedded_sources = (
        ("dataset.dataReleaseId", _mapping(manifest.get("dataset")).get("dataReleaseId")),
        (
            "dataset.semantic_contract.data_release_id",
            _mapping(_mapping(manifest.get("dataset")).get("semantic_contract")).get("data_release_id"),
        ),
        (
            "canonicalConfig.dataset.dataset_id",
            _mapping(_mapping(manifest.get("canonicalConfig")).get("dataset")).get("dataset_id"),
        ),
    )
    embedded = [
        (source, candidate)
        for source, raw_value in embedded_sources
        if (candidate := _validated_data_release_id(raw_value, source=source)) is not None
    ]
    identities = {candidate for _, candidate in embedded}
    if len(identities) > 1:
        detail = ", ".join(f"{source}={candidate}" for source, candidate in embedded)
        raise ValueError(f"Research manifest has conflicting DataRelease identities: {detail}")

    manifest_identity = next(iter(identities), None)
    override_identity = _validated_data_release_id(override, source="--data-release-id")
    if override_identity and manifest_identity and override_identity != manifest_identity:
        raise ValueError(
            "--data-release-id conflicts with the DataRelease identity declared by the research manifest"
        )
    if override_identity:
        return override_identity
    if manifest_identity:
        return manifest_identity
    raise ValueError("Research manifest is not bound to a DataRelease; supply --data-release-id")


def resolve_universe_release_id(manifest: Mapping[str, Any]) -> str | None:
    """Resolve one UniverseRelease identity from all supported manifest locations."""

    dataset = _mapping(manifest.get("dataset"))
    semantic_contract = _mapping(dataset.get("semantic_contract"))
    canonical_dataset = _mapping(_mapping(manifest.get("canonicalConfig")).get("dataset"))
    embedded_sources = (
        ("dataset.universeReleaseId", dataset.get("universeReleaseId")),
        ("dataset.semantic_contract.universe_release_id", semantic_contract.get("universe_release_id")),
        ("canonicalConfig.dataset.universe_release_id", canonical_dataset.get("universe_release_id")),
    )
    embedded = [
        (source, candidate)
        for source, raw_value in embedded_sources
        if (candidate := _validated_universe_release_id(raw_value, source=source)) is not None
    ]
    identities = {candidate for _, candidate in embedded}
    if len(identities) > 1:
        detail = ", ".join(f"{source}={candidate}" for source, candidate in embedded)
        raise ValueError(f"Research manifest has conflicting UniverseRelease identities: {detail}")
    return next(iter(identities), None)


def _promotion_status(manifest: Mapping[str, Any]) -> ResearchPromotionStatus:
    value = str(_mapping(manifest.get("promotion")).get("status") or "").upper()
    return {
        "PROMOTED": ResearchPromotionStatus.RESEARCH_PROMOTED,
        "RESEARCH_PROMOTED": ResearchPromotionStatus.RESEARCH_PROMOTED,
        "CANDIDATE": ResearchPromotionStatus.CANDIDATE,
        "SCREENED": ResearchPromotionStatus.RESEARCH_REVIEW,
        "RESEARCH_REVIEW": ResearchPromotionStatus.RESEARCH_REVIEW,
        "REJECTED": ResearchPromotionStatus.REJECTED,
    }.get(value, ResearchPromotionStatus.CANDIDATE)


def export_manifest_as_v2_bundle(
    manifest_path: str | Path,
    output_dir: str | Path,
    *,
    git_commit: str,
    container_digest: str,
    data_release_id: str | None = None,
) -> Path:
    source = Path(manifest_path).expanduser().resolve()
    source_bytes = source.read_bytes()
    source_manifest_sha256 = hashlib.sha256(source_bytes).hexdigest()
    if not _SHA256.fullmatch(source_manifest_sha256):
        raise RuntimeError("source manifest SHA-256 computation returned an invalid digest")
    manifest = json.loads(source_bytes)
    if not isinstance(manifest, Mapping):
        raise ValueError("Research manifest must be a JSON object")
    latest = _mapping(manifest.get("latestTargets"))
    raw_targets = latest.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ValueError("Research manifest has no promotable latestTargets")
    signal_date = str(latest.get("signalDate") or "")
    trade_date = str(latest.get("tradeDate") or "")
    targets = [dict(item) for item in raw_targets if isinstance(item, Mapping)]
    signals = [{"instrument": item.get("instrument"), "score": item.get("score")} for item in targets]
    canonical = _mapping(manifest.get("canonicalConfig"))
    policy = {
        "strategy": dict(_mapping(canonical.get("strategy"))),
        "portfolio": dict(_mapping(canonical.get("portfolio"))),
        "researchPromotion": dict(_mapping(canonical.get("promotion"))),
    }
    context = ResearchBundleContext(
        external_run_id=str(manifest.get("externalRunId") or source.parent.name),
        run_kind=str(manifest.get("runKind") or "research"),
        name=str(manifest.get("name") or "") or None,
        data_release_id=resolve_data_release_id(manifest, data_release_id),
        universe_release_id=resolve_universe_release_id(manifest),
        source_manifest_sha256=source_manifest_sha256,
        git_commit=git_commit,
        container_digest=container_digest,
        as_of_time=str(manifest.get("finishedAt") or f"{signal_date}T23:59:59+08:00"),
        signal_date=signal_date,
        trade_date=trade_date,
    )
    return export_research_bundle(
        output_dir,
        context=context,
        promotion_status=_promotion_status(manifest),
        model=dict(_mapping(manifest.get("model"))),
        strategy_policy=policy,
        signals=signals,
        targets=targets,
        validation={
            "metrics": dict(_mapping(manifest.get("metrics"))),
            "promotion": dict(_mapping(manifest.get("promotion"))),
            "sourceManifestSha256": source_manifest_sha256,
        },
    )

# Artifact Contract v3 producer -------------------------------------------------

V3_MANIFEST_NAME = "production_research_bundle_manifest_v3.json"
V3_ARTIFACT_TYPES = (
    "MODEL_RELEASE",
    "STRATEGY_POLICY",
    "SIGNAL_SNAPSHOT",
    "TARGET_PORTFOLIO",
    "VALIDATION_RESULT",
)
_V3_GIT_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
_V3_CONTAINER_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_V3_PRODUCER = "qlib-platform"
_V3_PROMOTION_STATUS = "RESEARCH_PROMOTED"


def _v3_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _v3_sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _v3_required_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _v3_required_text(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    return text


def _load_v3_source_manifest(source: Path) -> tuple[Mapping[str, Any], str]:
    raw = source.read_bytes()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Artifact Contract v3 source manifest must be valid JSON") from exc
    manifest = _v3_required_mapping(value, "Research manifest")
    return manifest, _v3_sha256_bytes(raw)


def _v3_contract_from_manifest(
    manifest: Mapping[str, Any],
) -> tuple[dict[str, Any], str, list[str]]:
    block = _v3_required_mapping(manifest.get("artifactContractV3"), "artifactContractV3")
    declared_version = block.get("schemaVersion")
    if declared_version not in (None, V3_SCHEMA_VERSION):
        raise ValueError(f"artifactContractV3.schemaVersion must be {V3_SCHEMA_VERSION}")
    policy_id = _v3_required_text(
        block.get("strategyPolicyId"),
        "artifactContractV3.strategyPolicyId",
    )
    instruction = _v3_required_mapping(
        block.get("targetInstruction"),
        "artifactContractV3.targetInstruction",
    )
    raw_parents = block.get("parentArtifactIds") or []
    if not isinstance(raw_parents, list):
        raise ValueError("artifactContractV3.parentArtifactIds must be an array")
    parents = [str(value).strip() for value in raw_parents]
    if any(not value for value in parents):
        raise ValueError("artifactContractV3.parentArtifactIds must not contain empty values")
    return canonicalize_target_instruction_v3(instruction), policy_id, parents


def _v3_promoted_status(manifest: Mapping[str, Any]) -> str:
    if _promotion_status(manifest) is not ResearchPromotionStatus.RESEARCH_PROMOTED:
        raise ValueError(
            "Artifact Contract v3 export requires promotion.status=RESEARCH_PROMOTED/PROMOTED"
        )
    return _V3_PROMOTION_STATUS


def _v3_lineage(manifest: Mapping[str, Any]) -> tuple[str, str]:
    data_release_id = resolve_data_release_id(manifest, None)
    universe_release_id = resolve_universe_release_id(manifest)
    if universe_release_id is None:
        raise ValueError("Artifact Contract v3 export requires an explicit UniverseRelease identity")
    return data_release_id, universe_release_id


def _v3_payloads(
    manifest: Mapping[str, Any],
    *,
    instruction: Mapping[str, Any],
    policy_id: str,
    source_manifest_sha256: str,
) -> dict[str, object]:
    target_rows = instruction["targets"]
    signals = [
        {
            "instrument": row["instrument"],
            "score": row.get("score"),
        }
        for row in target_rows
        if row.get("score") is not None
    ]
    return {
        "MODEL_RELEASE": {
            "model": dict(_mapping(manifest.get("model"))),
            "bestTrial": dict(_mapping(manifest.get("bestTrial"))),
        },
        "STRATEGY_POLICY": {
            "strategyPolicyId": policy_id,
            "strategyPolicySha256": instruction["policy"]["strategyPolicySha256"],
            "calendar": instruction["calendar"],
            "targetSemantics": instruction["targetSemantics"],
            "omittedInstrumentPolicy": instruction["omittedInstrumentPolicy"],
            "risk": instruction["risk"],
        },
        "SIGNAL_SNAPSHOT": {
            "asOfTime": instruction["timing"]["asOfTime"],
            "signalTime": instruction["timing"]["signalTime"],
            "signals": signals,
        },
        "TARGET_PORTFOLIO": dict(instruction),
        "VALIDATION_RESULT": {
            "sourceManifestSha256": source_manifest_sha256,
            "metrics": dict(_mapping(manifest.get("metrics"))),
            "promotion": dict(_mapping(manifest.get("promotion"))),
            "finalHoldoutAccess": dict(_mapping(manifest.get("finalHoldoutAccess"))),
        },
    }


def _v3_contract_identity(
    artifact_type: str,
    *,
    instruction: Mapping[str, Any],
    policy_id: str,
    git_commit: str,
    container_digest: str,
) -> dict[str, object]:
    identity: dict[str, object] = {
        "role": artifact_type,
        "producer": _V3_PRODUCER,
        "codeCommit": git_commit,
        "containerDigest": container_digest,
        "createdAt": instruction["timing"]["producedAt"],
        "strategyPolicyId": policy_id,
        "strategyPolicySha256": instruction["policy"]["strategyPolicySha256"],
        "calendarId": instruction["calendar"]["calendarId"],
        "calendarVersionSha256": instruction["calendar"]["versionSha256"],
    }
    if artifact_type == "TARGET_PORTFOLIO":
        identity.update(
            instructionType=instruction["instructionType"],
            targetSemantics=instruction["targetSemantics"],
            omittedInstrumentPolicy=instruction["omittedInstrumentPolicy"],
        )
    return identity


def _build_v3_bundle_files(
    *,
    manifest: Mapping[str, Any],
    source_manifest_sha256: str,
    git_commit: str,
    container_digest: str,
) -> dict[str, bytes]:
    if not _V3_GIT_COMMIT.fullmatch(git_commit):
        raise ValueError("git_commit must be a 40-64 character lowercase hexadecimal commit")
    if not _V3_CONTAINER_DIGEST.fullmatch(container_digest):
        raise ValueError("container_digest must be sha256:<64 lowercase hex>")

    status = _v3_promoted_status(manifest)
    data_release_id, universe_release_id = _v3_lineage(manifest)
    instruction, policy_id, root_parents = _v3_contract_from_manifest(manifest)
    payloads = _v3_payloads(
        manifest,
        instruction=instruction,
        policy_id=policy_id,
        source_manifest_sha256=source_manifest_sha256,
    )

    files: dict[str, bytes] = {}
    signed_artifacts: list[dict[str, object]] = []
    artifact_ids: dict[str, str] = {}
    parents = list(root_parents)
    for artifact_type in V3_ARTIFACT_TYPES:
        payload_path = f"payloads/{artifact_type.lower()}.json"
        payload_bytes = _v3_json_bytes(payloads[artifact_type])
        payload_sha = _v3_sha256_bytes(payload_bytes)
        contract_identity = _v3_contract_identity(
            artifact_type,
            instruction=instruction,
            policy_id=policy_id,
            git_commit=git_commit,
            container_digest=container_digest,
        )
        artifact_id, _ = build_artifact_identity_v3(
            artifact_type=artifact_type,
            promotion_status=status,
            data_release_id=data_release_id,
            universe_release_id=universe_release_id,
            source_manifest_sha256=source_manifest_sha256,
            payload_sha256=payload_sha,
            parent_artifact_ids=parents,
            contract_identity=contract_identity,
        )
        payload_ref = {
            "mediaType": "application/json",
            "relativePath": payload_path,
            "sha256": payload_sha,
        }
        validate_portable_payload_ref(payload_ref)
        envelope = {
            "schemaVersion": V3_SCHEMA_VERSION,
            "artifactVersion": V3_SCHEMA_VERSION,
            "artifactId": artifact_id,
            "type": artifact_type,
            "status": status,
            "createdAt": instruction["timing"]["producedAt"],
            "producer": _V3_PRODUCER,
            "codeCommit": git_commit,
            "containerDigest": container_digest,
            "dataReleaseId": data_release_id,
            "universeReleaseId": universe_release_id,
            "parentArtifactIds": list(parents),
            "sourceManifestSha256": source_manifest_sha256,
            "payloadSha256": payload_sha,
            "payload": payload_ref,
            "contractIdentity": contract_identity,
        }
        envelope_bytes = _v3_json_bytes(envelope)
        artifact_path = f"artifacts/{artifact_id}.json"
        files[payload_path] = payload_bytes
        files[artifact_path] = envelope_bytes
        signed_artifacts.append(
            {
                "artifactId": artifact_id,
                "type": artifact_type,
                "status": status,
                "relativePath": artifact_path,
                "artifactSha256": _v3_sha256_bytes(envelope_bytes),
                "payloadSha256": payload_sha,
                "dataReleaseId": data_release_id,
                "universeReleaseId": universe_release_id,
            }
        )
        artifact_ids[artifact_type] = artifact_id
        parents = [artifact_id]

    target_identity = _v3_contract_identity(
        "TARGET_PORTFOLIO",
        instruction=instruction,
        policy_id=policy_id,
        git_commit=git_commit,
        container_digest=container_digest,
    )
    bundle_manifest = {
        "schemaVersion": V3_SCHEMA_VERSION,
        "artifactVersion": V3_SCHEMA_VERSION,
        "artifactContractVersion": V3_SCHEMA_VERSION,
        "status": status,
        "createdAt": instruction["timing"]["producedAt"],
        "producer": _V3_PRODUCER,
        "codeCommit": git_commit,
        "containerDigest": container_digest,
        "dataReleaseId": data_release_id,
        "universeReleaseId": universe_release_id,
        "parentArtifactIds": root_parents,
        "sourceManifestSha256": source_manifest_sha256,
        "artifactIdsByType": artifact_ids,
        "contractIdentity": target_identity,
        "signedArtifacts": signed_artifacts,
    }
    if tuple(str(row["type"]) for row in signed_artifacts) != V3_ARTIFACT_TYPES:
        raise ValueError("Artifact Contract v3 export did not produce the required artifact set")
    files[V3_MANIFEST_NAME] = _v3_json_bytes(bundle_manifest)
    return files


def _v3_directory_matches(root: Path, expected: Mapping[str, bytes]) -> bool:
    if not root.is_dir():
        return False
    actual_files = {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if set(actual_files) != set(expected):
        return False
    return all(actual_files[path].read_bytes() == content for path, content in expected.items())


def _publish_v3_bundle(output_dir: Path, expected: Mapping[str, bytes]) -> None:
    if output_dir.exists():
        if _v3_directory_matches(output_dir, expected):
            return
        raise FileExistsError("Artifact Contract v3 output exists with non-identical contents")

    import shutil
    import tempfile

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=str(output_dir.parent)))
    try:
        for relative_path, content in expected.items():
            destination = staging / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        if not _v3_directory_matches(staging, expected):
            raise RuntimeError("Artifact Contract v3 staging verification failed")
        if output_dir.exists():
            if _v3_directory_matches(output_dir, expected):
                return
            raise FileExistsError(
                "Artifact Contract v3 output appeared with non-identical contents"
            )
        try:
            staging.replace(output_dir)
        except OSError:
            if output_dir.exists() and _v3_directory_matches(output_dir, expected):
                return
            raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def export_manifest_as_v3_bundle(
    manifest_path: str | Path,
    output_dir: str | Path,
    *,
    git_commit: str,
    container_digest: str,
) -> Path:
    """Export one immutable, portable Artifact Contract v3 research bundle.

    ``artifactContractV3.targetInstruction`` is mandatory. The producer does
    not infer calendar, FX, time, cash/no-signal/revoke, or snapshot/delta
    semantics from legacy research fields.
    """

    source = Path(manifest_path).expanduser().resolve()
    manifest, source_manifest_sha256 = _load_v3_source_manifest(source)
    expected = _build_v3_bundle_files(
        manifest=manifest,
        source_manifest_sha256=source_manifest_sha256,
        git_commit=git_commit,
        container_digest=container_digest,
    )
    output = Path(output_dir).expanduser().resolve()
    _publish_v3_bundle(output, expected)
    return output / V3_MANIFEST_NAME
