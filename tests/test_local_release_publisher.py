from __future__ import annotations

import json
from pathlib import Path

import pytest

from tushare_qlib.dataset_registry import DatasetRegistry
from tushare_qlib.releases import FileReleaseStore, LocalReleasePublisher, import_qlib_dataset
from tushare_qlib.releases.capabilities import ReleaseCapabilityError, assert_release_capability
from tushare_qlib.settings import Settings


def _provider(root: Path) -> Path:
    (root / "calendars").mkdir(parents=True)
    (root / "instruments").mkdir()
    (root / "features" / "sh600000").mkdir(parents=True)
    (root / "calendars" / "day.txt").write_text("2026-08-21\n2026-08-24\n", encoding="utf-8")
    (root / "instruments" / "all.txt").write_text("SH600000\t2026-08-21\t2026-08-24\n", encoding="utf-8")
    (root / "features" / "sh600000" / "close.day.bin").write_bytes(b"\x00\x00\x80?")
    return root


def _settings(tmp_path: Path) -> Settings:
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "\n".join(
            [
                "mode: standalone",
                f"project_root: {tmp_path / 'data'}",
                "data_source: {kind: auto}",
                "storage: {registry_path: ''}",
                "release_store: {kind: file, root: ''}",
                "qlib:",
                "  dataset_dir: ''",
                "  versions_root: ''",
                "  dataset_name: test_cn",
                "  dataset_ref: research-current",
            ]
        ),
        encoding="utf-8",
    )
    return Settings.load(config)


def test_qlib_import_is_immutable_idempotent_and_exploratory(tmp_path: Path):
    source = _provider(tmp_path / "legacy")
    publisher = LocalReleasePublisher(tmp_path / "releases")

    first = publisher.import_qlib(source)
    second = publisher.import_qlib(source)
    original_manifest = first.manifest_path.read_bytes()
    (source / "features" / "sh600000" / "close.day.bin").write_bytes(b"changed")

    assert second.data_release_id == first.data_release_id
    assert first.manifest_path.read_bytes() == original_manifest
    assert (
        first.manifest_path.parent / "components" / "qlib_dataset" / "features" / "sh600000" / "close.day.bin"
    ).read_bytes() != b"changed"
    with pytest.raises(ReleaseCapabilityError, match="exploratory"):
        assert_release_capability(first, "phase3")


def test_import_registers_bound_dataset_and_atomic_aliases(tmp_path: Path):
    settings = _settings(tmp_path)
    source = _provider(tmp_path / "legacy")

    release, dataset = import_qlib_dataset(settings, source)
    registry = DatasetRegistry(settings.registry_path)

    assert (
        FileReleaseStore(tmp_path / "data" / "releases").resolve(release.data_release_id).manifest_sha256
        == release.manifest_sha256
    )
    assert dataset.data_release_id == release.data_release_id
    assert registry.resolve_release_alias("research-release-current") == release.data_release_id
    assert registry.resolve("research-current").version_id == dataset.version_id
    payload = json.loads(dataset.manifest_path.read_text(encoding="utf-8"))
    assert payload["semantic_contract"]["governance_level"] == "exploratory"


def test_import_rejects_incomplete_qlib_provider(tmp_path: Path):
    provider = tmp_path / "incomplete"
    provider.mkdir()

    with pytest.raises(ValueError, match="calendars/day.txt"):
        LocalReleasePublisher(tmp_path / "releases").import_qlib(provider)
