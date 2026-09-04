from __future__ import annotations

import json
import warnings

import pandas as pd
import pytest

from qlib_platform.data.store import sha256_file
from qlib_platform.research.research_timing import shared_research_calendar
from qlib_platform.settings import Paths, Settings


def _settings(tmp_path, qlib_dir):
    return Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={"mode": "standalone", "data_source": {"kind": "auto"}, "qlib": {}},
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=qlib_dir,
    )


def _write_dataset_calendar(qlib_dir, *, digest: str | None = None) -> str:
    calendar = qlib_dir / "calendars" / "day.txt"
    calendar.parent.mkdir(parents=True)
    calendar.write_text("2024-01-02\n2024-01-03\n", encoding="utf-8")
    version_id = "a" * 64
    manifest = {
        "schema_version": "3.0",
        "dataset_name": "cn_standalone",
        "layer": "qlib",
        "version_id": version_id,
        "status": "VALIDATED",
        "data_path": str(qlib_dir.resolve()),
        "partitions": [
            {
                "path": "calendars/day.txt",
                "bytes": calendar.stat().st_size,
                "sha256": digest or sha256_file(calendar),
            }
        ],
        "semantic_contract": {},
        "parents": [],
    }
    (qlib_dir / "dataset_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return version_id


def test_versioned_standalone_calendar_does_not_require_mutable_raw_store(tmp_path):
    qlib_dir = tmp_path / "qlib-version"
    _write_dataset_calendar(qlib_dir)
    settings = _settings(tmp_path, qlib_dir)

    actual = shared_research_calendar(settings)

    assert actual.tolist() == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")]
    assert not settings.paths.raw.exists()


def test_versioned_standalone_calendar_fails_closed_on_manifest_checksum_drift(tmp_path):
    qlib_dir = tmp_path / "qlib-version"
    _write_dataset_calendar(qlib_dir, digest="0" * 64)
    settings = _settings(tmp_path, qlib_dir)

    with pytest.raises(ValueError, match="calendar checksum mismatch"):
        shared_research_calendar(settings)


def test_standalone_profile_no_longer_emits_top_level_tushare_deprecation(monkeypatch):
    for name in (
        "QUANT_DATA_ROOT",
        "DATASET_RELEASE_ID",
        "QLIB_REPO",
        "QLIB_DATA_URI",
        "QLIB_DATA_ROOT",
        "TUSHARE_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("qlib_platform.settings.load_dotenv", lambda: None)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        settings = Settings.load("configs/pipeline.standalone.yaml", create_dirs=False)

    assert not any("top-level tushare configuration is deprecated" in str(item.message) for item in caught)
    assert settings.data_source_config["tushare"]["calls_per_minute"] == 180
    assert settings.data_source_config["runtime"]["max_attempts"] == 6
    assert settings.data_source_config["optional_endpoints"]["moneyflow"] is True
