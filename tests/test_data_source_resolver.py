from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import tushare_qlib.data_source_resolver as resolver_module
from tushare_qlib.data_source_resolver import resolve_source
from tushare_qlib.releases import import_qlib_dataset
from tushare_qlib.settings import Settings


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
                "qlib: {dataset_dir: '', versions_root: '', dataset_ref: research-current}",
            ]
        ),
        encoding="utf-8",
    )
    return Settings.load(config)


def _provider(root: Path) -> Path:
    (root / "calendars").mkdir(parents=True)
    (root / "instruments").mkdir()
    (root / "features" / "sh600000").mkdir(parents=True)
    (root / "calendars" / "day.txt").write_text("2026-08-24\n", encoding="utf-8")
    (root / "instruments" / "all.txt").write_text(
        "SH600000\t2026-08-24\t2026-08-24\n", encoding="utf-8"
    )
    (root / "features" / "sh600000" / "close.day.bin").write_bytes(b"close")
    return root


def test_resolver_reports_data_unavailable_without_failing_startup(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr("tushare_qlib.settings.load_dotenv", lambda: None)
    settings = _settings(tmp_path)

    result = resolve_source(settings)

    assert result.status == "DATA_UNAVAILABLE"
    assert result.source is None


def test_resolver_discovers_existing_qlib_provider(tmp_path: Path):
    settings = _settings(tmp_path)
    (settings.qlib_data_uri / "calendars").mkdir(parents=True)
    (settings.qlib_data_uri / "instruments").mkdir()
    (settings.qlib_data_uri / "features").mkdir()
    (settings.qlib_data_uri / "calendars" / "day.txt").write_text("2026-08-24\n", encoding="utf-8")

    result = resolve_source(settings)

    assert result.status == "IMPORT_REQUIRED"
    assert result.source == "qlib"
    assert result.path == settings.qlib_data_uri


def test_resolver_prefers_local_raw_before_download(tmp_path: Path):
    settings = _settings(tmp_path)
    (settings.paths.raw / "daily").mkdir(parents=True)

    result = resolve_source(settings)

    assert result.status == "DATA_INCOMPLETE"
    assert result.source == "local_raw"
    assert set(result.missing_components) == {
        "bars",
        "adjustment_factors",
        "security_master",
        "trading_calendar",
    }


def test_resolver_selects_exploratory_market_profile_for_minimal_raw(tmp_path: Path):
    settings = _settings(tmp_path)
    (settings.paths.raw / "daily").mkdir(parents=True)
    (settings.paths.raw / "adj_factor").mkdir()
    settings.paths.metadata.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"value": [1]}).to_parquet(settings.paths.raw / "daily" / "data.parquet")
    pd.DataFrame({"value": [1]}).to_parquet(settings.paths.raw / "adj_factor" / "data.parquet")
    pd.DataFrame({"value": [1]}).to_parquet(settings.paths.metadata / "stock_master.parquet")
    pd.DataFrame({"value": [1]}).to_parquet(settings.paths.metadata / "trade_calendar.parquet")

    result = resolve_source(settings)

    assert result.status == "BUILD_REQUIRED"
    assert result.profile == "ashare_market_import_v1"
    assert "pit_fundamentals_source" in result.missing_components


def test_resolver_ready_probe_uses_bounded_sampled_verification(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    import_qlib_dataset(settings, _provider(tmp_path / "legacy"))
    dataset_calls: list[dict[str, object]] = []
    release_calls: list[dict[str, object]] = []
    real_dataset_verify = resolver_module.verify_dataset_manifest
    real_release_resolve = resolver_module.FileReleaseStore.resolve

    def tracked_dataset_verify(path, **kwargs):
        dataset_calls.append(dict(kwargs))
        return real_dataset_verify(path, **kwargs)

    def tracked_release_resolve(self, reference, **kwargs):
        release_calls.append(dict(kwargs))
        return real_release_resolve(self, reference, **kwargs)

    monkeypatch.setattr(resolver_module, "verify_dataset_manifest", tracked_dataset_verify)
    monkeypatch.setattr(resolver_module.FileReleaseStore, "resolve", tracked_release_resolve)

    result = resolver_module.resolve_source(settings)

    assert result.status == "READY"
    assert release_calls == [{"mode": "sampled", "sample_size": 64, "workers": 4}]
    assert dataset_calls == [{"mode": "sampled", "sample_size": 64, "workers": 4}]


def test_resolver_rejects_corrupt_active_dataset(tmp_path: Path):
    settings = _settings(tmp_path)
    _, dataset = import_qlib_dataset(settings, _provider(tmp_path / "legacy"))
    partition = dataset.data_path / "calendars" / "day.txt"
    partition.write_text("tampered\n", encoding="utf-8")

    with pytest.raises(ValueError, match="checksum mismatch|size drift"):
        resolve_source(settings)
