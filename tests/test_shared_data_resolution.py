from __future__ import annotations

import os
from pathlib import Path

import pytest

from tushare_qlib.bootstrap import bootstrap
from tushare_qlib.data_source_resolver import ReleaseSelectionRequired, resolve_source
from tushare_qlib.dataset_manifest import write_dataset_manifest
from tushare_qlib.dataset_registry import DatasetRegistry
from tushare_qlib.dataset_resolver import resolve_dataset
from tushare_qlib.releases import (
    FileReleaseStore,
    LocalReleasePublisher,
    import_qlib_dataset,
    release_store_root,
)
from tushare_qlib.settings import Settings


def _settings(
    config: Path,
    *,
    project_root: str | Path,
    dataset_name: str,
    dataset_ref: str,
) -> Settings:
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        "\n".join(
            [
                "mode: standalone",
                f"project_root: {project_root}",
                "data_source: {kind: auto}",
                "storage: {registry_path: ''}",
                "release_store: {kind: file, root: ''}",
                "qlib:",
                "  dataset_dir: ''",
                "  versions_root: ''",
                f"  dataset_name: {dataset_name}",
                f"  dataset_ref: {dataset_ref}",
            ]
        ),
        encoding="utf-8",
    )
    return Settings.load(config)


def _provider(root: Path, date: str, payload: bytes) -> Path:
    (root / "calendars").mkdir(parents=True)
    (root / "instruments").mkdir()
    feature_root = root / "features" / "sh600000"
    feature_root.mkdir(parents=True)
    (root / "calendars" / "day.txt").write_text(f"{date}\n", encoding="utf-8")
    (root / "instruments" / "all.txt").write_text(f"sh600000\t{date}\t{date}\n", encoding="utf-8")
    (feature_root / "close.day.bin").write_bytes(payload)
    return root


def test_standalone_read_reuses_shared_research_alias_before_release_selection(tmp_path: Path) -> None:
    shared_root = tmp_path / "shared-data"
    research = _settings(
        tmp_path / "configs" / "research.yaml",
        project_root=shared_root,
        dataset_name="cn_tushare",
        dataset_ref="research-current",
    )
    _, first = import_qlib_dataset(
        research,
        _provider(tmp_path / "provider-a", "2026-08-20", b"provider-a"),
    )
    _, active = import_qlib_dataset(
        research,
        _provider(tmp_path / "provider-b", "2026-08-21", b"provider-b"),
    )
    assert first.version_id != active.version_id
    assert len(list(FileReleaseStore(release_store_root(research)).list())) == 2

    # Simulate a shared data root whose immutable releases remain present but whose
    # release alias has not been selected.  The published DatasetVersion alias is
    # already sufficient to identify the research input unambiguously.
    registry = DatasetRegistry(research.registry_path)
    with registry.connect() as connection:
        connection.execute("DELETE FROM release_aliases WHERE alias='research-release-current'")

    standalone = _settings(
        tmp_path / "configs" / "standalone.yaml",
        project_root=shared_root,
        dataset_name="cn_standalone",
        dataset_ref="standalone-current",
    )

    source = resolve_source(standalone)
    resolved = resolve_dataset(standalone, "standalone-current", allow_legacy=False)

    assert source.status == "READY"
    assert source.source == "dataset_version"
    assert source.reference == "research-current"
    assert resolved.reference == "research-current"
    assert resolved.version_id == active.version_id
    assert resolved.dataset_name == "cn_tushare"


def test_manifested_current_provider_beats_multiple_unaliased_releases(tmp_path: Path) -> None:
    shared_root = tmp_path / "shared-data"
    standalone = _settings(
        tmp_path / "configs" / "standalone.yaml",
        project_root=shared_root,
        dataset_name="cn_standalone",
        dataset_ref="standalone-current",
    )
    current = _provider(shared_root / "qlib" / "current", "2026-08-22", b"shared-current")
    _, payload = write_dataset_manifest(
        current,
        dataset_name="cn_tushare",
        layer="qlib",
        semantic_contract={"source_type": "shared-current"},
        coverage={"start": "2026-08-22", "end": "2026-08-22"},
        final_data_path=current,
    )

    publisher = LocalReleasePublisher(release_store_root(standalone))
    publisher.import_qlib(_provider(tmp_path / "release-a", "2026-08-20", b"release-a"))
    publisher.import_qlib(_provider(tmp_path / "release-b", "2026-08-21", b"release-b"))
    assert len(list(FileReleaseStore(release_store_root(standalone)).list())) == 2

    source = resolve_source(standalone)
    resolved = resolve_dataset(standalone, "standalone-current", allow_legacy=False)

    assert source.status == "READY"
    assert source.source == "dataset_current"
    assert source.reference == "standalone-current"
    assert source.path == current.resolve()
    assert resolved.reference == "standalone-current"
    assert resolved.version_id == payload["version_id"]
    assert resolved.dataset_name == "cn_tushare"
    assert resolved.data_path == current.resolve()


def test_legacy_current_provider_is_imported_before_release_history_selection(
    tmp_path: Path,
) -> None:
    shared_root = tmp_path / "shared-data"
    standalone = _settings(
        tmp_path / "configs" / "standalone.yaml",
        project_root=shared_root,
        dataset_name="cn_standalone",
        dataset_ref="standalone-current",
    )
    current = _provider(shared_root / "qlib" / "current", "2026-08-22", b"legacy-current")

    publisher = LocalReleasePublisher(release_store_root(standalone))
    publisher.import_qlib(_provider(tmp_path / "release-a", "2026-08-20", b"release-a"))
    publisher.import_qlib(_provider(tmp_path / "release-b", "2026-08-21", b"release-b"))
    assert len(list(FileReleaseStore(release_store_root(standalone)).list())) == 2

    source = resolve_source(standalone)
    assert source.status == "IMPORT_REQUIRED"
    assert source.source == "qlib"
    assert source.reference == "legacy"
    assert source.path == current.resolve()
    assert source.action == "release import-qlib"

    with pytest.raises(KeyError, match="unknown dataset reference: standalone-current"):
        resolve_dataset(standalone, "standalone-current", allow_legacy=False)

    prepared = bootstrap(standalone, source="auto")
    assert prepared["status"] == "READY"
    assert prepared["source"] == "qlib"

    resolved_source = resolve_source(standalone)
    resolved = resolve_dataset(standalone, "standalone-current", allow_legacy=False)
    assert resolved_source.status == "READY"
    assert resolved_source.source == "data_release"
    assert resolved.reference == "standalone-current"
    assert resolved.version_id == prepared["datasetVersionId"]
    assert resolved.dataset_name == "cn_standalone"
    assert resolved.data_path.parent == standalone.qlib_versions_root
    assert resolved.manifest_path.is_file()


def test_multiple_releases_without_alias_or_current_manifest_still_fail_closed(tmp_path: Path) -> None:
    standalone = _settings(
        tmp_path / "configs" / "standalone.yaml",
        project_root=tmp_path / "shared-data",
        dataset_name="cn_standalone",
        dataset_ref="standalone-current",
    )
    publisher = LocalReleasePublisher(release_store_root(standalone))
    publisher.import_qlib(_provider(tmp_path / "release-a", "2026-08-20", b"release-a"))
    publisher.import_qlib(_provider(tmp_path / "release-b", "2026-08-21", b"release-b"))

    with pytest.raises(ReleaseSelectionRequired, match="multiple DataReleases"):
        resolve_source(standalone)


def test_explicit_unknown_reference_does_not_use_shared_alias_fallback(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path / "configs" / "standalone.yaml",
        project_root=tmp_path / "data",
        dataset_name="cn_standalone",
        dataset_ref="standalone-current",
    )
    current = _provider(settings.qlib_data_uri, "2026-08-22", b"shared-current")
    write_dataset_manifest(
        current,
        dataset_name="cn_tushare",
        layer="qlib",
        semantic_contract={"source_type": "shared-current"},
        coverage={"start": "2026-08-22", "end": "2026-08-22"},
        final_data_path=current,
    )

    with pytest.raises(KeyError, match="unknown dataset reference: missing-version"):
        resolve_dataset(settings, "missing-version", allow_legacy=False)


@pytest.mark.skipif(os.name == "nt", reason="symlink creation is not reliably permitted on Windows CI")
def test_project_root_symlink_resolves_to_shared_data_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    shared_root = tmp_path / "other-repo" / "data"
    shared_root.mkdir(parents=True)
    repo.mkdir()
    (repo / "data").symlink_to(shared_root, target_is_directory=True)

    settings = _settings(
        repo / "configs" / "pipeline.standalone.yaml",
        project_root="./data",
        dataset_name="cn_standalone",
        dataset_ref="standalone-current",
    )

    assert settings.paths.root == shared_root.resolve()
    assert settings.registry_path == (shared_root / "registry" / "qlib.sqlite").resolve()
    assert settings.qlib_versions_root == (shared_root / "qlib" / "versions").resolve()
