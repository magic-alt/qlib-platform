from __future__ import annotations

import json
from pathlib import Path

from qlib_platform.datasets.dataset_registry import DatasetRegistry
from qlib_platform.datasets.lakehouse import freeze_pipeline_layers
from qlib_platform.settings import Paths, Settings


def test_freeze_pipeline_layers_publishes_complete_lineage(tmp_path: Path):
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    settings = Settings(
        tmp_path / "pipeline.yaml",
        {"qlib": {"dataset_name": "test"}},
        paths,
        None,
        None,
        tmp_path / "legacy",
    )
    for path, value in (
        (paths.raw / "daily" / "trade_date=20260102" / "data.parquet", b"bronze"),
        (paths.curated / "trade_date=20260102" / "data.parquet", b"silver"),
        (paths.staging_full / "SH600000.parquet", b"gold"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)

    snapshots = freeze_pipeline_layers(
        settings,
        mode="full",
        gold_sources=(("qlib_input", paths.staging_full),),
    )

    assert [item["layer"] for item in snapshots] == ["bronze", "silver", "gold"]
    registry = DatasetRegistry(settings.registry_path)
    assert registry.resolve("bronze-current").version_id == snapshots[0]["version_id"]
    assert registry.resolve("silver-current").version_id == snapshots[1]["version_id"]
    assert registry.resolve("gold-current").version_id == snapshots[2]["version_id"]
    gold_manifest = json.loads(Path(str(snapshots[2]["manifest_path"])).read_text(encoding="utf-8"))
    assert gold_manifest["parents"][0]["version_id"] == snapshots[1]["version_id"]


def test_registry_rebuild_restores_published_aliases(tmp_path: Path):
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    settings = Settings(
        tmp_path / "pipeline.yaml",
        {"qlib": {"dataset_name": "test"}},
        paths,
        None,
        None,
        tmp_path / "legacy",
    )
    for path in (paths.raw / "x", paths.curated / "x", paths.staging_full / "x"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    snapshots = freeze_pipeline_layers(
        settings, mode="full", gold_sources=(("qlib_input", paths.staging_full),)
    )
    settings.registry_path.unlink()

    rebuilt = DatasetRegistry(settings.registry_path)
    assert rebuilt.rebuild(paths.root) == 3
    assert rebuilt.resolve("gold-current").version_id == snapshots[-1]["version_id"]
    assert (settings.registry_path.parent / "aliases" / "gold-current.json").is_file()
