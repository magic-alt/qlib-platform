from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping

from qlib_platform.canonical_config import CanonicalConfig
from qlib_platform.settings import Settings
from qlib_platform.data.store import sha256_file
from qlib_platform.data.universe import membership_fingerprint


_QLIB_COMPAT_VERSION = "0.9.7"
_QLIB_PACKAGE_MARKER = ".qlib-platform-package-identity.json"


def sha256_json(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _package_qlib_identity() -> dict[str, str] | None:
    try:
        distribution = importlib.metadata.distribution("pyqlib")
    except importlib.metadata.PackageNotFoundError:
        return None
    version = str(distribution.version)
    record = distribution.read_text("RECORD") or ""
    record_sha = hashlib.sha256(record.encode("utf-8")).hexdigest()
    return {
        "distribution": "pyqlib",
        "version": version,
        "recordSha256": record_sha,
        "identity": f"pyqlib=={version}:record:{record_sha}",
    }


def _package_qlib_compat_root() -> Path | None:
    identity = _package_qlib_identity()
    if identity is None or identity["version"] != _QLIB_COMPAT_VERSION:
        return None
    root = (
        Path(tempfile.gettempdir())
        / "qlib-platform"
        / f"pyqlib-{identity['version']}-{identity['recordSha256'][:16]}"
    )
    scripts = root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    marker = root / _QLIB_PACKAGE_MARKER
    marker.write_text(json.dumps(identity, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    wrapper = scripts / "dump_bin.py"
    wrapper.write_text(
        "from qlib_platform.datasets.qlib_dump_cli import main\n\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n",
        encoding="utf-8",
    )
    return root


def git_revision(path: Path | None) -> dict[str, object]:
    if path is None or not path.exists():
        return {"commit": None, "dirty": None}
    marker = path / _QLIB_PACKAGE_MARKER
    if marker.is_file():
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"commit": None, "dirty": None}
        identity = str(payload.get("identity") or "").strip()
        return {
            "commit": identity or None,
            "dirty": False if identity else None,
            "source": "package",
            "version": payload.get("version"),
            "recordSha256": payload.get("recordSha256"),
        }
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
    return {"commit": commit, "dirty": dirty, "source": "git"}


def _supplies_imported_qlib(checkout: Path, origin: Path) -> bool:
    """Return whether *checkout* owns the imported ``qlib`` package source.

    A wheel installed under ``<application>/.venv`` is physically nested below the
    application's Git repository. Walking parent directories and accepting the first
    ``.git`` would therefore misidentify the application revision as the Qlib
    revision. A real Qlib checkout owns ``qlib/__init__.py`` at the exact imported
    origin (including editable installs).
    """

    candidate_origin = checkout / "qlib" / "__init__.py"
    if not candidate_origin.is_file():
        return False
    try:
        return candidate_origin.resolve() == origin.resolve()
    except OSError:
        return False


def resolve_qlib_repo(configured: Path | None) -> Path | None:
    """Resolve the active Qlib implementation root.

    A real Git checkout is preferred when it actually supplies the imported ``qlib``
    package.  For the supported packaged installation, ``pyqlib==0.9.7`` is sufficient:
    a temporary compatibility root exposes the maintained dump entry point and carries
    a stable wheel RECORD identity.  This keeps ``QLIB_REPO`` optional without ever
    treating the enclosing qlib-platform repository as the Qlib revision.
    """

    try:
        spec = importlib.util.find_spec("qlib")
    except (ImportError, AttributeError, ValueError):
        return None
    if spec is None or spec.origin is None:
        return None
    origin = Path(spec.origin).resolve()

    if configured is not None and configured.exists():
        configured = configured.resolve()
        if (configured / ".git").exists() and _supplies_imported_qlib(configured, origin):
            return configured

    package_path = origin.parent
    for candidate in (package_path, *package_path.parents):
        if (candidate / ".git").exists() and _supplies_imported_qlib(candidate, origin):
            return candidate
    return _package_qlib_compat_root()


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
    qlib_git = git_revision(resolve_qlib_repo(settings.qlib_repo))
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
        "membershipSnapshotSha256": dataset_manifest.get("universe_membership_sha256"),
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
    universe_membership_matches_dataset = True
    if config.dataset.membership_type == "point_in_time":
        current_membership_hash = membership_fingerprint(settings)
        required["universeMembershipSha256"] = current_membership_hash
        required["datasetUniverseMembershipSha256"] = dataset_manifest.get("universe_membership_sha256")
        universe_payload["currentMembershipSha256"] = current_membership_hash
        universe_membership_matches_dataset = current_membership_hash == dataset_manifest.get(
            "universe_membership_sha256"
        )
    required_fields_complete = all(value not in {None, "", "unversioned"} for value in required.values())
    qlib_commit_matches_dataset = bool(qlib_git.get("commit")) and qlib_git.get(
        "commit"
    ) == dataset_manifest.get("qlib_git_commit")
    complete = (
        required_fields_complete
        and qlib_commit_matches_dataset
        and universe_membership_matches_dataset
        and platform_git.get("dirty") is False
        and qlib_git.get("dirty") is False
    )
    payload: dict[str, object] = {
        **required,
        "qlibPlatformDirty": platform_git.get("dirty"),
        "qlibDirty": qlib_git.get("dirty"),
        "qlibIdentitySource": qlib_git.get("source"),
        "qlibPackageVersion": qlib_git.get("version"),
        "qlibPackageRecordSha256": qlib_git.get("recordSha256"),
        "requiredFieldsComplete": required_fields_complete,
        "featureColumns": feature_columns,
        "modelParameters": config.model.parameters,
        "universe": universe_payload,
        "qlibCommitMatchesDataset": qlib_commit_matches_dataset,
        "universeMembershipMatchesDataset": universe_membership_matches_dataset,
        "complete": complete,
    }
    payload["lineageId"] = sha256_json(payload)[:32]
    return payload


def dirty_research_override_enabled(settings: Settings, lineage: Mapping[str, object]) -> bool:
    """Allow known dirty revisions for research without granting release authority."""

    research = settings.data.get("research")
    configured = bool(research.get("allow_dirty_research", False)) if isinstance(research, Mapping) else False
    platform_dirty = lineage.get("qlibPlatformDirty")
    qlib_dirty = lineage.get("qlibDirty")
    dirty_is_known = platform_dirty in {True, False} and qlib_dirty in {True, False}
    return bool(
        configured
        and dirty_is_known
        and (platform_dirty is True or qlib_dirty is True)
        and lineage.get("requiredFieldsComplete") is True
        and lineage.get("qlibCommitMatchesDataset") is True
        and lineage.get("universeMembershipMatchesDataset", True) is True
    )
