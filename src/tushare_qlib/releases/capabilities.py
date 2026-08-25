from __future__ import annotations

from ..data_release import DataRelease, QLIB_IMPORT_PROFILE


class ReleaseCapabilityError(ValueError):
    pass


def governance_level(release: DataRelease) -> str:
    policies = release.manifest.get("policies", {})
    if isinstance(policies, dict) and policies.get("governanceLevel"):
        return str(policies["governanceLevel"])
    return "exploratory" if release.profile == QLIB_IMPORT_PROFILE else "research"


def assert_release_capability(release: DataRelease, capability: str) -> None:
    if governance_level(release) == "exploratory" and capability in {
        "phase2",
        "phase3",
        "artifact_v2_export",
        "target_portfolio",
        "research_promotion",
    }:
        raise ReleaseCapabilityError(
            f"DataRelease {release.data_release_id} is exploratory and cannot perform {capability}"
        )
