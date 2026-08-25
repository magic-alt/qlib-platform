from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from ..data_release import DataRelease, MARKET_IMPORT_PROFILE, QLIB_IMPORT_PROFILE
from ..dataset_registry import DatasetRegistry
from ..settings import Settings
from .file_store import FileReleaseStore
from .publisher import release_store_root


class ReleaseCapabilityError(ValueError):
    pass


def governance_level(release: DataRelease) -> str:
    return manifest_governance_level(release.manifest)


def manifest_governance_level(manifest: Mapping[str, Any]) -> str:
    policies = manifest.get("policies", {})
    if isinstance(policies, dict) and policies.get("governanceLevel"):
        return str(policies["governanceLevel"])
    profile = str(manifest.get("profile") or "")
    return "exploratory" if profile in {QLIB_IMPORT_PROFILE, MARKET_IMPORT_PROFILE} else "research"


_POLICY_KEYS = {
    "phase2": ("phase2Allowed", "phase2Phase3Allowed"),
    "phase3": ("phase3Allowed", "phase2Phase3Allowed"),
    "artifact_v2_export": ("artifactV2Allowed", "certifiedPromotionAllowed"),
    "target_portfolio": ("targetPortfolioAllowed",),
    "research_promotion": ("researchPromotionAllowed", "promotionAllowed"),
}


def assert_manifest_capability(manifest: Mapping[str, Any], capability: str) -> None:
    if capability not in _POLICY_KEYS:
        raise ValueError(f"unknown DataRelease capability: {capability}")
    release_id = str(manifest.get("dataReleaseId") or "<unbound>")
    policies = manifest.get("policies", {})
    policies = policies if isinstance(policies, Mapping) else {}
    decisions = [policies[key] for key in _POLICY_KEYS[capability] if key in policies]
    if any(value is not True for value in decisions):
        raise ReleaseCapabilityError(
            f"DataRelease {release_id} ({manifest_governance_level(manifest)}) "
            f"policy forbids capability {capability}"
        )
    if not decisions and manifest_governance_level(manifest) == "exploratory":
        raise ReleaseCapabilityError(
            f"DataRelease {release_id} is exploratory and cannot perform {capability}"
        )


def assert_release_capability(release: DataRelease, capability: str) -> None:
    assert_manifest_capability(release.manifest, capability)


def require_release_capability(
    settings: Settings,
    capability: str,
    *,
    reference: str | None = None,
) -> DataRelease:
    selected = reference
    if selected is None:
        selected = DatasetRegistry(settings.registry_path).resolve_release_alias("research-release-current")
    if not selected and settings.uses_data_release():
        configured = settings.data_release_config
        selected = str(configured.get("id") or configured.get("ref") or "").strip() or None
    if not selected:
        raise ReleaseCapabilityError(
            f"DataRelease capability {capability} requires an explicitly bound current release"
        )
    release = FileReleaseStore(release_store_root(settings)).resolve(selected)
    assert_release_capability(release, capability)
    return release


def data_release_id_from_manifest(manifest: Mapping[str, Any]) -> str:
    from ..research_bundle_export import resolve_data_release_id

    return resolve_data_release_id(manifest, None)


def data_release_id_from_artifact(path: str | Path) -> str:
    from ..artifacts import ArtifactType, load_artifact_manifest, validate_artifact

    source = Path(path).expanduser().resolve()
    frame = pd.read_csv(source)
    values = frame.get("artifact_type", pd.Series(dtype=str)).dropna()
    artifact_type = str(values.iloc[0]) if len(values) else ""
    expected = {
        ArtifactType.MODEL_TOPK.value: ArtifactType.MODEL_TOPK,
        ArtifactType.TARGET_PORTFOLIO.value: ArtifactType.TARGET_PORTFOLIO,
    }.get(artifact_type)
    if expected is None:
        raise ReleaseCapabilityError(f"artifact has no supported release binding: {artifact_type!r}")
    metadata = validate_artifact(frame, expected)
    return data_release_id_from_manifest(load_artifact_manifest(metadata))


def data_release_id_from_bundle(path: str | Path) -> str:
    source = Path(path).expanduser().resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ReleaseCapabilityError("Artifact v2 bundle must be a JSON object")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ReleaseCapabilityError("Artifact v2 bundle contains no artifacts")
    release_ids = {str(item.get("dataReleaseId") or "") for item in artifacts if isinstance(item, Mapping)}
    if len(release_ids) != 1:
        raise ReleaseCapabilityError("Artifact v2 bundle must bind exactly one DataRelease")
    release_id = next(iter(release_ids))
    if not release_id.startswith("ds_") or len(release_id) != 67:
        raise ReleaseCapabilityError("Artifact v2 bundle DataRelease binding is invalid")
    return release_id
