from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Mapping

from .canonical_config import CanonicalConfig
from .settings import Settings
from .store import sha256_file


def sha256_json(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def git_revision(path: Path | None) -> dict[str, object]:
    if path is None or not path.exists():
        return {"commit": None, "dirty": None}
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"], cwd=path, check=True, capture_output=True, text=True
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}
    return {"commit": commit, "dirty": dirty}


def build_lineage(
    settings: Settings,
    config: CanonicalConfig,
    *,
    dataset_fingerprint: str,
    feature_columns: list[str],
) -> dict[str, object]:
    dataset_manifest_path = settings.qlib_data_uri / "dataset_manifest.json"
    dataset_manifest: Mapping[str, object] = {}
    if dataset_manifest_path.is_file():
        loaded = json.loads(dataset_manifest_path.read_text(encoding="utf-8"))
        dataset_manifest = loaded if isinstance(loaded, Mapping) else {}
    project_root = Path(__file__).resolve().parents[2]
    platform_git = git_revision(project_root)
    qlib_git = git_revision(settings.qlib_repo)
    config_hash = sha256_file(settings.config_path) if settings.config_path.is_file() else None
    dataset_manifest_hash = sha256_file(dataset_manifest_path) if dataset_manifest_path.is_file() else None
    feature_hash = sha256_json(feature_columns)
    model_hash = sha256_json(config.model.parameters)
    universe_payload = {
        "name": config.dataset.universe_name,
        "membershipType": config.dataset.membership_type,
        "source": config.dataset.source,
        "secondaryFilters": config.dataset.secondary_filters,
        "sourceSnapshotId": dataset_manifest.get("source_snapshot_id")
        or dataset_manifest.get("staging_manifest_sha256"),
    }
    required = {
        "qlibPlatformCommit": platform_git.get("commit"),
        "qlibCommit": qlib_git.get("commit"),
        "datasetQlibCommit": dataset_manifest.get("qlib_git_commit"),
        "configSha256": config_hash,
        "datasetFingerprint": dataset_fingerprint if dataset_fingerprint != "unversioned" else None,
        "datasetManifestSha256": dataset_manifest_hash,
        "sourceSnapshotId": universe_payload["sourceSnapshotId"],
        "featureSchemaSha256": feature_hash,
        "modelParametersSha256": model_hash,
        "universeSpecSha256": sha256_json(universe_payload),
    }
    complete = all(value not in {None, "", "unversioned"} for value in required.values())
    payload: dict[str, object] = {
        **required,
        "qlibPlatformDirty": platform_git.get("dirty"),
        "qlibDirty": qlib_git.get("dirty"),
        "featureColumns": feature_columns,
        "modelParameters": config.model.parameters,
        "universe": universe_payload,
        "qlibCommitMatchesDataset": bool(qlib_git.get("commit"))
        and qlib_git.get("commit") == dataset_manifest.get("qlib_git_commit"),
        "complete": complete,
    }
    payload["complete"] = bool(payload["complete"]) and bool(payload["qlibCommitMatchesDataset"])
    payload["lineageId"] = sha256_json(payload)[:32]
    return payload
