from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import qlib_platform.bootstrap as bootstrap_module
from qlib_platform.bootstrap import _materialize_selected_release
from qlib_platform.datasets.data_release import DataRelease
from qlib_platform.datasets.qlib_staging_contract import QlibStagingContractError
from qlib_platform.datasets.qlib_staging_repair import (
    StagingRepairResult,
    inspect_qlib_staging_inventory,
    repair_transient_qlib_staging_release,
)
from qlib_platform.settings import Paths, Settings


def _settings(tmp_path: Path) -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    return Settings(
        config_path=tmp_path / "configs" / "pipeline.standalone.yaml",
        data={
            "mode": "standalone",
            "release_store": {"active_keep": 1},
            "qlib": {
                "dataset_name": "cn_standalone",
                "dataset_ref": "standalone-current",
            },
        },
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=paths.root / "qlib" / "current",
    )


def _release_with_staging(
    settings: Settings,
    *,
    nested_dir: str = ".curated_by_symbol/symbol=SH600000",
    malformed_top_level: bool = False,
) -> DataRelease:
    store_root = settings.paths.root / "releases"
    release_dir = store_root / ("ds_" + "a" * 64)
    staging = release_dir / "components" / "qlib_staging"
    staging.mkdir(parents=True)
    canonical = staging / ("00000.parquet" if malformed_top_level else "SH600000.parquet")
    canonical_frame = {
        "date": pd.to_datetime(["2026-09-01", "2026-09-02"]),
        "close": [10.0, 10.1],
    }
    if not malformed_top_level:
        canonical_frame["symbol"] = ["SH600000", "SH600000"]
    pd.DataFrame(canonical_frame).to_parquet(canonical, index=False)

    files = [{"path": f"components/qlib_staging/{canonical.name}"}]
    if not malformed_top_level:
        nested = staging / nested_dir / "00000.parquet"
        nested.parent.mkdir(parents=True)
        # DuckDB PARTITION_BY(symbol) omits the partition key from the file itself.
        pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-09-01"]),
                "close": [10.0],
            }
        ).to_parquet(nested, index=False)
        files.append(
            {
                "path": (
                    "components/qlib_staging/"
                    + nested.relative_to(staging).as_posix()
                )
            }
        )

    manifest_path = release_dir / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    manifest = {
        "dataReleaseId": release_dir.name,
        "manifestSha256": "b" * 64,
        "profile": "ashare_qlib_research_v2",
        "assetClass": "equity",
        "market": "china",
        "universe": "CSI300",
        "benchmark": "SH000300",
        "coverage": {"start": "2026-09-01", "end": "2026-09-02"},
        "asOfTime": "2026-09-02T17:30:00+08:00",
        "policies": {"governanceLevel": "research"},
        "lineage": {"producer": "qlib-platform"},
    }
    components = {
        "qlib_staging": {
            "role": "qlib_staging",
            "datasetKey": "qlib_staging",
            "schemaVersion": "qlib-staging-v2",
            "files": files,
        }
    }
    return DataRelease(store_root, manifest_path, manifest, components)


def test_inventory_recognizes_duckdb_partition_as_transient(tmp_path: Path) -> None:
    release = _release_with_staging(_settings(tmp_path))

    inventory = inspect_qlib_staging_inventory(release)

    assert [path.name for path in inventory.canonical] == ["SH600000.parquet"]
    assert len(inventory.transient) == 1
    assert ".curated_by_symbol" in inventory.transient[0].as_posix()


def test_repair_derives_clean_child_release_without_local_raw(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _settings(tmp_path)
    release = _release_with_staging(settings)
    captured: dict[str, object] = {}

    def fake_publish(_publisher, **kwargs):
        captured.update(kwargs)
        staging_source = next(
            item.source for item in kwargs["components"] if item.role == "qlib_staging"
        )
        assert sorted(path.name for path in staging_source.glob("*.parquet")) == [
            "SH600000.parquet"
        ]
        assert not list(staging_source.rglob("00000.parquet"))
        return release

    monkeypatch.setattr(
        "qlib_platform.datasets.qlib_staging_repair.LocalReleasePublisher.publish",
        fake_publish,
    )

    result = repair_transient_qlib_staging_release(settings, release)

    assert result is not None
    assert result.release is release
    assert result.ignored_transient_files == (
        ".curated_by_symbol/symbol=SH600000/00000.parquet",
    )
    lineage = captured["lineage"]
    assert lineage["parentReleaseId"] == release.data_release_id
    assert lineage["repairReason"] == "qlib_staging_transient_inventory"
    assert lineage["ignoredTransientFileCount"] == 1


def test_unknown_nested_staging_remains_fail_closed(tmp_path: Path) -> None:
    release = _release_with_staging(_settings(tmp_path), nested_dir="unexpected/symbol=SH600000")

    with pytest.raises(QlibStagingContractError, match="unsupported nested parquet"):
        inspect_qlib_staging_inventory(release)


def test_malformed_top_level_chunk_is_not_silently_dropped(tmp_path: Path) -> None:
    release = _release_with_staging(_settings(tmp_path), malformed_top_level=True)

    with pytest.raises(QlibStagingContractError, match="missing=\\['symbol'\\]"):
        inspect_qlib_staging_inventory(release)


def test_materializer_uses_derived_release_before_raw_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _settings(tmp_path)
    parent = _release_with_staging(settings)
    child_dir = settings.paths.root / "releases" / ("ds_" + "c" * 64)
    child_manifest = child_dir / "manifest.json"
    child_manifest.parent.mkdir(parents=True, exist_ok=True)
    child_manifest.write_text("{}", encoding="utf-8")
    child = DataRelease(
        settings.paths.root / "releases",
        child_manifest,
        {
            "dataReleaseId": child_dir.name,
            "manifestSha256": "d" * 64,
            "profile": "ashare_qlib_import_v1",
            "coverage": {"start": "2026-09-01", "end": "2026-09-02"},
        },
        {"qlib_dataset": {"files": []}},
    )

    class FakeStore:
        def __init__(self, _root: Path):
            pass

        def resolve(self, reference: str, **_kwargs):
            return parent if reference == parent.data_release_id else child

    monkeypatch.setattr(bootstrap_module, "FileReleaseStore", FakeStore)
    monkeypatch.setattr(
        bootstrap_module,
        "repair_transient_qlib_staging_release",
        lambda _settings, release: (
            StagingRepairResult(
                child,
                (".curated_by_symbol/symbol=SH600000/00000.parquet",),
            )
            if release.data_release_id == parent.data_release_id
            else None
        ),
    )
    monkeypatch.setattr(
        bootstrap_module,
        "import_qlib_dataset",
        lambda _settings, _source: (child, SimpleNamespace(version_id="dv_child")),
    )
    monkeypatch.setattr(bootstrap_module, "_archive_standalone_history", lambda *_args: 0)

    result = _materialize_selected_release(settings, parent.data_release_id)

    assert result["status"] == "READY"
    assert result["dataReleaseId"] == child.data_release_id
    assert result["datasetVersionId"] == "dv_child"
    assert result["recoveredFromIncompatibleRelease"] is True
    assert result["recoveryReason"] == "qlib_staging_transient_inventory"
    assert result["parentDataReleaseId"] == parent.data_release_id
    assert result["ignoredTransientFiles"] == [
        ".curated_by_symbol/symbol=SH600000/00000.parquet"
    ]
