from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from ..lineage import sha256_json
from ..platform_release import QLIB_RESEARCH_PROFILE_V2, load_platform_release
from ..settings import Settings


REQUIRED_V2_ACCEPTANCE_CHECKS = (
    "PIT_LEAKAGE",
    "PIT_INDUSTRY",
    "PIT_FUNDAMENTALS",
    "DATASET_BUILD",
    "FEATURE_SNAPSHOT_CHECKSUM",
    "LABEL_TIMING",
    "RIDGE_DETERMINISTIC",
    "MINI_QLIB_LEAN_E2E",
)


def write_data_release_v2_acceptance(
    settings: Settings,
    *,
    evidence: Mapping[str, Mapping[str, object]],
    output: str | Path,
) -> Path:
    """Seal the deliberately narrow DataRelease v2 acceptance evidence."""

    release = load_platform_release(settings)
    if release.profile != QLIB_RESEARCH_PROFILE_V2:
        raise ValueError("Phase 2 data acceptance requires ashare_qlib_research_v2")
    missing = sorted(set(REQUIRED_V2_ACCEPTANCE_CHECKS) - set(evidence))
    if missing:
        raise ValueError(f"DataRelease v2 acceptance evidence is missing checks: {missing}")
    normalized: dict[str, dict[str, object]] = {}
    failures: list[str] = []
    for name in REQUIRED_V2_ACCEPTANCE_CHECKS:
        item = evidence[name]
        status = str(item.get("status") or "")
        artifact_sha = str(item.get("artifactSha256") or "").lower()
        if status != "PASS":
            failures.append(name)
        if len(artifact_sha) != 64 or any(character not in "0123456789abcdef" for character in artifact_sha):
            raise ValueError(f"DataRelease v2 acceptance check has invalid artifact hash: {name}")
        normalized[name] = {
            "status": status,
            "artifactSha256": artifact_sha,
            "details": item.get("details"),
        }
    if failures:
        raise ValueError(f"DataRelease v2 acceptance checks failed: {failures}")
    payload: dict[str, Any] = {
        "schemaVersion": "phase2_data_release_acceptance_v1",
        "dataReleaseId": release.data_release_id,
        "manifestSha256": release.manifest_sha256,
        "profile": release.profile,
        "checks": normalized,
        "passed": True,
        "scope": "NARROW_V2_DELTA_ACCEPTANCE",
        "fullInfrastructureRecertificationRun": False,
        "publishingAuthorized": False,
    }
    payload["acceptanceSha256"] = sha256_json(payload)
    target = Path(output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        if existing != payload:
            raise ValueError("existing DataRelease v2 acceptance artifact differs")
        return target
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return target
