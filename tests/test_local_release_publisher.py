from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import pytest

from tushare_qlib.dataset_registry import DatasetRegistry
from tushare_qlib.releases import (
    FileReleaseStore,
    LocalReleasePublisher,
    import_qlib_dataset,
    publish_local_market_release,
)
from tushare_qlib.releases.capabilities import (
    ReleaseCapabilityError,
    assert_manifest_capability,
    assert_release_capability,
)
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
    assert first.manifest["asOfTime"] == "2026-08-24T17:30:00+08:00"
    assert first.manifest_path.read_bytes() == original_manifest
    assert (
        first.manifest_path.parent / "components" / "qlib_dataset" / "features" / "sh600000" / "close.day.bin"
    ).read_bytes() != b"changed"
    with pytest.raises(ReleaseCapabilityError, match="exploratory"):
        assert_release_capability(first, "phase3")


def test_release_and_dataset_use_content_addressed_copy_on_write(tmp_path: Path):
    settings = _settings(tmp_path)
    source = _provider(tmp_path / "legacy")

    release, dataset = import_qlib_dataset(settings, source)

    frozen = release.manifest_path.parent / "components" / "qlib_dataset" / "calendars" / "day.txt"
    digest = next(
        item["sha256"]
        for component in release.manifest["components"]
        for item in component["files"]
        if item["path"].endswith("calendars/day.txt")
    )
    stored = release.manifest_path.parent.parent / "objects" / digest[:2] / digest
    materialized = dataset.data_path / "calendars" / "day.txt"
    assert os.path.samefile(frozen, stored)
    assert os.path.samefile(materialized, frozen)


def test_content_addressed_object_corruption_fails_closed(tmp_path: Path):
    source = _provider(tmp_path / "legacy")
    publisher = LocalReleasePublisher(tmp_path / "releases")
    release = publisher.import_qlib(source)
    entry = release.manifest["components"][0]["files"][0]
    stored = tmp_path / "releases" / "objects" / entry["sha256"][:2] / entry["sha256"]
    stored.write_bytes(b"tampered")

    with pytest.raises(ValueError, match="object is corrupt"):
        publisher.import_qlib(source)


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


def test_explicit_policy_false_blocks_research_governance_release():
    with pytest.raises(ReleaseCapabilityError, match="policy forbids"):
        assert_manifest_capability(
            {
                "dataReleaseId": "ds_" + "a" * 64,
                "profile": "ashare_qlib_research_v2",
                "policies": {
                    "governanceLevel": "research",
                    "targetPortfolioAllowed": False,
                },
            },
            "target_portfolio",
        )


def test_market_import_publishes_bound_exploratory_qlib_dataset(tmp_path: Path):
    settings = _settings(tmp_path)
    dates = pd.bdate_range("2026-08-17", periods=5)
    symbols = ("600000.SH", "000001.SZ")
    bars: list[dict[str, object]] = []
    factors: list[dict[str, object]] = []
    for position, date in enumerate(dates):
        for index, symbol in enumerate(symbols):
            close = 10.0 + index + position * 0.1
            bars.append(
                {
                    "trade_date": date,
                    "ts_code": symbol,
                    "open": close - 0.02,
                    "high": close + 0.05,
                    "low": close - 0.05,
                    "close": close,
                    "volume": 1_000_000 + index,
                    "amount": close * (1_000_000 + index),
                }
            )
            factors.append({"trade_date": date, "ts_code": symbol, "adj_factor": 1.0})
    daily = settings.paths.raw / "daily"
    adjustment = settings.paths.raw / "adj_factor"
    daily.mkdir(parents=True)
    adjustment.mkdir()
    pd.DataFrame(bars).to_parquet(daily / "data.parquet", index=False)
    pd.DataFrame(factors).to_parquet(adjustment / "data.parquet", index=False)
    settings.paths.metadata.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"ts_code": list(symbols)}).to_parquet(
        settings.paths.metadata / "stock_master.parquet", index=False
    )
    pd.DataFrame({"cal_date": dates, "is_open": 1}).to_parquet(
        settings.paths.metadata / "trade_calendar.parquet", index=False
    )

    release, dataset = publish_local_market_release(
        settings,
        start=str(dates[0].date()),
        end=str(dates[-1].date()),
    )

    assert release.profile == "ashare_market_import_v1"
    assert dataset.data_release_id == release.data_release_id
    assert (dataset.data_path / "features" / "sh600000" / "close.day.bin").is_file()
    with pytest.raises(ReleaseCapabilityError, match="policy forbids"):
        assert_release_capability(release, "target_portfolio")
