from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

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
