from pathlib import Path

import pandas as pd

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


def test_logical_hash_ignores_row_order_and_archives_changed_partition(tmp_path: Path):
    store = PartitionStore(tmp_path / "raw")
    revisions = tmp_path / "revisions"
    first = pd.DataFrame(
        {"ts_code": ["000001.SZ", "600000.SH"], "trade_date": ["20260810", "20260810"], "close": [10.0, 9.0]}
    )
    reordered = first.iloc[::-1].reset_index(drop=True)
    assert frame_content_sha256(first, key_columns=("ts_code", "trade_date")) == frame_content_sha256(
        reordered, key_columns=("ts_code", "trade_date")
    )

    _, changed, first_hash = store.write_if_changed(
        "daily", "20260810", first, revision_root=revisions
    )
    assert changed is True
    _, changed, _ = store.write_if_changed(
        "daily", "20260810", reordered, revision_root=revisions
    )
    assert changed is False

    revised = first.copy()
    revised.loc[0, "close"] = 10.1
    store.write_if_changed("daily", "20260810", revised, revision_root=revisions)
    assert (revisions / "daily" / "trade_date=20260810" / first_hash / "data.parquet").is_file()
