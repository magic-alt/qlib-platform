from __future__ import annotations

import os
from pathlib import Path

import pytest

from tushare_qlib.data_source_resolver import resolve_source
from tushare_qlib.dataset_registry import DatasetRegistry
from tushare_qlib.dataset_resolver import resolve_dataset
from tushare_qlib.releases import FileReleaseStore, import_qlib_dataset, release_store_root
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
    (root / "instruments" / "all.txt").write_text(
        f"sh600000\t{date}\t{date}\n", encoding="utf-8"
    )
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


def test_explicit_unknown_reference_does_not_use_shared_alias_fallback(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path / "configs" / "standalone.yaml",
        project_root=tmp_path / "data",
        dataset_name="cn_standalone",
        dataset_ref="standalone-current",
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
