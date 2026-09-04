from __future__ import annotations

import json
from pathlib import Path

import pytest

import qlib_platform.datasets.layout_migration as migration_module
from qlib_platform.datasets.dataset_registry import DatasetRegistry
from qlib_platform.datasets.layout_migration import LayoutMigrator
from qlib_platform.settings import Paths, Settings


def _settings(tmp_path: Path) -> Settings:
    root = tmp_path / "data"
    return Settings(
        tmp_path / "pipeline.yaml",
        {"qlib": {"dataset_version": "legacy", "dataset_name": "test"}},
        Paths.from_root(root),
        None,
        None,
        root / "qlib" / "legacy",
    )


def test_paths_use_provider_neutral_market_bronze_root(tmp_path: Path):
    paths = Paths.from_root(tmp_path / "data")

    assert paths.bronze == tmp_path / "data" / "bronze" / "market"
    assert paths.raw == paths.bronze / "current"
    assert paths.legacy_vendor_bronze == tmp_path / "data" / "bronze" / "tushare"
    assert paths.legacy_vendor_raw == paths.legacy_vendor_bronze / "current"


def test_migration_dry_run_does_not_create_new_layout(tmp_path: Path):
    settings = _settings(tmp_path)
    source = settings.paths.root / "raw" / "daily"
    source.mkdir(parents=True)
    (source / "part.parquet").write_bytes(b"data")

    plan = LayoutMigrator(settings).plan()

    assert plan["dry_run"] is True
    assert plan["total_files"] == 1
    assert not settings.paths.raw.exists()


def test_migration_apply_preserves_old_tree_and_is_resumable(tmp_path: Path):
    settings = _settings(tmp_path)
    source = settings.paths.root / "raw" / "daily"
    source.mkdir(parents=True)
    (source / "part.parquet").write_bytes(b"data")
    migrator = LayoutMigrator(settings)

    journal = migrator.apply("test-migration")
    repeated = migrator.apply("test-migration")

    assert repeated == journal
    assert (source / "part.parquet").read_bytes() == b"data"
    assert (settings.paths.raw / "daily" / "part.parquet").read_bytes() == b"data"
    payload = json.loads(journal.read_text(encoding="utf-8"))
    assert payload["steps"][0]["source_preserved"] is True
    assert payload["steps"][0]["verified_files"] == 1


def test_provider_named_bronze_migrates_without_mutating_legacy_identity(tmp_path: Path):
    settings = _settings(tmp_path)
    source = settings.paths.legacy_vendor_bronze
    raw = source / "current" / "daily"
    raw.mkdir(parents=True)
    (raw / "part.parquet").write_bytes(b"data")
    legacy_manifest = source / "dataset_manifest.json"
    legacy_payload = b'{"version_id":"ds_legacy_identity","pit_semantics":"unchanged"}'
    legacy_manifest.write_bytes(legacy_payload)

    journal = LayoutMigrator(settings).apply("provider-neutral-bronze")

    assert legacy_manifest.read_bytes() == legacy_payload
    assert (settings.paths.bronze / "dataset_manifest.json").read_bytes() == legacy_payload
    assert (settings.paths.raw / "daily" / "part.parquet").read_bytes() == b"data"
    payload = json.loads(journal.read_text(encoding="utf-8"))
    step = payload["steps"][0]
    assert step["source"] == str(settings.paths.legacy_vendor_bronze)
    assert step["target"] == str(settings.paths.bronze)
    assert step["source_preserved"] is True


def test_migration_fails_closed_when_two_legacy_market_layouts_exist(tmp_path: Path):
    settings = _settings(tmp_path)
    (settings.paths.legacy_vendor_bronze / "current").mkdir(parents=True)
    (settings.paths.root / "raw").mkdir(parents=True)

    with pytest.raises(RuntimeError, match="ambiguous legacy market-data layouts"):
        LayoutMigrator(settings).plan()


def test_migration_resumes_when_interrupted_during_materialization(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    source = settings.paths.root / "raw" / "daily"
    source.mkdir(parents=True)
    (source / "part.parquet").write_bytes(b"data")
    migrator = LayoutMigrator(settings)
    original = migration_module._clone_immutable_tree
    calls = {"count": 0}

    def fail_once(source_path: Path, target_path: Path) -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("injected interruption")
        original(source_path, target_path)

    monkeypatch.setattr(migration_module, "_clone_immutable_tree", fail_once)
    try:
        migrator.apply("interrupted")
    except RuntimeError:
        pass

    assert (source / "part.parquet").read_bytes() == b"data"
    journal = migrator.apply("interrupted")

    assert journal.is_file()
    assert (settings.paths.raw / "daily" / "part.parquet").read_bytes() == b"data"


def test_migration_quarantines_primary_backups_and_unresolved_research(tmp_path: Path):
    settings = _settings(tmp_path)
    for name in ("legacy", "legacy.backup.20260101T000000Z"):
        source = settings.paths.root / "qlib" / name
        source.mkdir(parents=True)
        (source / "data.bin").write_bytes(name.encode())
    research = settings.paths.output / "research" / "run-1" / "manifest.json"
    research.parent.mkdir(parents=True)
    research.write_text('{"schemaVersion":"2.0","externalRunId":"run-1"}', encoding="utf-8")

    journal_path = LayoutMigrator(settings).apply("legacy-assets")
    journal = json.loads(journal_path.read_text(encoding="utf-8"))

    assert len(journal["legacy_qlib_versions"]) == 2
    assert journal["legacy_research_records"] == 1
    assert (settings.paths.root / "qlib" / "legacy" / "data.bin").is_file()
    assert (settings.paths.root / "qlib" / "legacy.backup.20260101T000000Z" / "data.bin").is_file()
    registry = DatasetRegistry(settings.registry_path)
    assert all(item.status == "QUARANTINED" for item in registry.list_versions())
    with registry.connect() as connection:
        record = connection.execute("SELECT status FROM legacy_records").fetchone()
    assert record["status"] == "LEGACY_UNRESOLVED"
