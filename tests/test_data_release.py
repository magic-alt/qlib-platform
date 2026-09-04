from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_platform_release import _settings, _write_release
from qlib_platform.datasets.data_release import load_data_release
from qlib_platform.datasets.dataset_manifest import write_dataset_manifest
from qlib_platform.datasets.dataset_registry import DatasetRegistry
from qlib_platform.releases import FileReleaseStore


def test_generic_and_legacy_release_apis_resolve_identical_contract(tmp_path: Path):
    manifest, release_id = _write_release(tmp_path)
    settings = _settings(tmp_path, manifest, release_id)
    settings.data["data_source"] = {
        "kind": "data_release",
        "data_release": {
            "id": release_id,
            "data_root": str(tmp_path),
            "manifest": str(manifest),
        },
    }

    generic = load_data_release(settings)
    stored = FileReleaseStore(tmp_path / "releases").resolve(release_id)

    assert generic.data_release_id == stored.data_release_id == release_id
    assert generic.manifest_sha256 == stored.manifest_sha256
    assert generic.components.keys() == stored.components.keys()


def test_file_release_store_resolves_alias_without_mutating_release(tmp_path: Path):
    manifest, release_id = _write_release(tmp_path)
    aliases = tmp_path / "releases" / "aliases"
    aliases.mkdir()
    pointer = aliases / "research-release-current.json"
    pointer.write_text(json.dumps({"dataReleaseId": release_id}), encoding="utf-8")
    original = manifest.read_bytes()

    release = FileReleaseStore(tmp_path / "releases").resolve("research-release-current")

    assert release.data_release_id == release_id
    assert manifest.read_bytes() == original


def test_data_release_schema_rejects_unknown_top_level_field(tmp_path: Path):
    manifest, release_id = _write_release(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["certification"] = {"level": "platform-certified"}
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="schema validation failed"):
        FileReleaseStore(tmp_path / "releases").resolve(release_id)


def test_registry_promotes_release_and_bound_dataset_atomically(tmp_path: Path):
    manifest, release_id = _write_release(tmp_path)
    release = FileReleaseStore(tmp_path / "releases").resolve(release_id)
    dataset_root = tmp_path / "dataset"
    dataset_root.mkdir()
    (dataset_root / "value.bin").write_bytes(b"data")
    dataset_manifest, payload = write_dataset_manifest(
        dataset_root,
        dataset_name="bound",
        layer="qlib",
        semantic_contract={
            "data_release_id": release_id,
            "data_release_manifest_sha256": release.manifest_sha256,
        },
    )
    payload["status"] = "VALIDATED"
    dataset_manifest.write_text(json.dumps(payload), encoding="utf-8")
    registry = DatasetRegistry(tmp_path / "registry.sqlite")
    registry.register_release(release)
    version = registry.register_dataset(payload, dataset_manifest)

    registry.promote_research_snapshot(
        release_alias="research-release-current",
        data_release_id=release_id,
        dataset_alias="research-current",
        dataset_version_id=version.version_id,
    )

    assert registry.resolve_release_alias("research-release-current") == release_id
    assert registry.resolve("research-current").data_release_id == release_id


def test_atomic_snapshot_promotion_rejects_dataset_release_drift(tmp_path: Path):
    manifest, release_id = _write_release(tmp_path)
    release = FileReleaseStore(tmp_path / "releases").resolve(release_id)
    dataset_root = tmp_path / "dataset"
    dataset_root.mkdir()
    (dataset_root / "value.bin").write_bytes(b"data")
    dataset_manifest, payload = write_dataset_manifest(
        dataset_root,
        dataset_name="unbound",
        layer="qlib",
        semantic_contract={"data_release_id": "ds_" + "0" * 64},
    )
    registry = DatasetRegistry(tmp_path / "registry.sqlite")
    registry.register_release(release)
    version = registry.register_dataset(payload, dataset_manifest)

    with pytest.raises(ValueError, match="not bound"):
        registry.promote_research_snapshot(
            release_alias="research-release-current",
            data_release_id=release_id,
            dataset_alias="research-current",
            dataset_version_id=version.version_id,
        )

    assert registry.resolve_release_alias("research-release-current") is None
