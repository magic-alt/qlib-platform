import json
from pathlib import Path

import pandas as pd

from tushare_qlib.quality import assert_quality, validate_raw_store
from tushare_qlib.store import PartitionStore, frame_content_sha256


def test_failed_partition_is_retryable(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pd.DataFrame, "to_parquet", lambda self, path, index=False: self.to_pickle(path))
    monkeypatch.setattr(pd, "read_parquet", lambda path: pd.read_pickle(path))
    store = PartitionStore(tmp_path)
    store.write_status("daily", "20260806", status="failed", metadata={"error": "temporary"})
    assert not store.is_terminal("daily", "20260806")
    store.write("daily", "20260806", pd.DataFrame({"x": [1]}), status="success")
    assert store.is_terminal("daily", "20260806")
    assert store.read_manifest("daily", "20260806")["sha256"]


def test_logical_hash_ignores_row_order_and_replaces_current_partition(tmp_path: Path):
    store = PartitionStore(tmp_path / "raw")
    first = pd.DataFrame(
        {"ts_code": ["000001.SZ", "600000.SH"], "trade_date": ["20260810", "20260810"], "close": [10.0, 9.0]}
    )
    reordered = first.iloc[::-1].reset_index(drop=True)
    assert frame_content_sha256(first, key_columns=("ts_code", "trade_date")) == frame_content_sha256(
        reordered, key_columns=("ts_code", "trade_date")
    )

    _, changed, _ = store.write_if_changed("daily", "20260810", first)
    assert changed is True
    _, changed, _ = store.write_if_changed("daily", "20260810", reordered)
    assert changed is False

    revised = first.copy()
    revised.loc[0, "close"] = 10.1
    store.write_if_changed("daily", "20260810", revised)
    assert store.read("daily", "20260810")["close"].tolist() == [10.1, 9.0]


def test_write_if_changed_accepts_legacy_file_hash_manifest_without_rewrite(tmp_path: Path):
    store = PartitionStore(tmp_path / "raw")
    frame = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260810"], "close": [10.0]})
    store.write("daily", "20260810", frame, metadata={"content_hash_kind": "legacy_file_v1"})

    _, changed, _ = store.write_if_changed("daily", "20260810", frame.copy())

    assert changed is False


def test_bronze_current_does_not_create_parallel_revision_tree(tmp_path: Path):
    store = PartitionStore(tmp_path / "bronze" / "tushare" / "current")
    first = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260810"], "close": [10.0]})
    second = first.assign(close=11.0)

    store.write("daily", "20260810", first)
    store.write("daily", "20260810", second)

    assert store.read("daily", "20260810")["close"].item() == 11.0
    assert not (tmp_path / "bronze" / "tushare" / "revisions").exists()


def _raw_frame(dataset: str, trade_date: str) -> pd.DataFrame:
    common = {"ts_code": ["000001.SZ"], "trade_date": [trade_date]}
    if dataset == "daily":
        return pd.DataFrame(
            {
                **common,
                "open": [10.0],
                "high": [10.5],
                "low": [9.5],
                "close": [10.2],
                "vol": [100.0],
                "amount": [1000.0],
            }
        )
    if dataset == "adj_factor":
        return pd.DataFrame({**common, "adj_factor": [1.0]})
    return pd.DataFrame(common)


def test_raw_store_integrity_checks_calendar_and_deep_partition(tmp_path: Path):
    store = PartitionStore(tmp_path / "current")
    for dataset in ("daily", "adj_factor", "daily_basic"):
        store.write(dataset, "20260810", _raw_frame(dataset, "20260810"))

    report = validate_raw_store(
        store,
        expected_dates=["20260810"],
        deep_dates=["20260810"],
    )

    assert report.passed
    assert_quality(report)


def test_raw_store_integrity_rejects_missing_date_and_tampered_manifest(tmp_path: Path):
    store = PartitionStore(tmp_path / "current")
    for dataset in ("daily", "adj_factor", "daily_basic"):
        store.write(dataset, "20260810", _raw_frame(dataset, "20260810"))
    manifest_path = store.manifest_path("daily", "20260810")
    manifest = store.read_manifest("daily", "20260810")
    manifest["rows"] = 99
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = validate_raw_store(
        store,
        expected_dates=["20260810", "20260811"],
        deep_dates=["20260810"],
    )

    assert not report.passed
    failures = {result.name for result in report.results if not result.passed}
    assert "daily_calendar_coverage" in failures
    assert "daily_20260810_row_count" in failures
