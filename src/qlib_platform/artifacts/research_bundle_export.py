from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from qlib_platform.artifacts.institutional_artifacts import (
    ResearchBundleContext,
    ResearchPromotionStatus,
    export_research_bundle,
)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def resolve_data_release_id(manifest: Mapping[str, Any], override: str | None) -> str:
    candidates = [
        override,
        _mapping(manifest.get("dataset")).get("dataReleaseId"),
        _mapping(_mapping(manifest.get("dataset")).get("semantic_contract")).get("data_release_id"),
        _mapping(_mapping(manifest.get("canonicalConfig")).get("dataset")).get("dataset_id"),
    ]
    for value in candidates:
        candidate = str(value or "").strip()
        if candidate.startswith("ds_") and len(candidate) == 67:
            return candidate
    raise ValueError("Research manifest is not bound to a DataRelease; supply --data-release-id")


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
    manifest = json.loads(source.read_text(encoding="utf-8"))
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
        universe_release_id=str(_mapping(manifest.get("dataset")).get("universeReleaseId") or "") or None,
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
            "sourceManifestSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        },
    )
