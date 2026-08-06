from pathlib import Path

import pandas as pd

from tushare_qlib.store import PartitionStore


def test_failed_partition_is_retryable(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pd.DataFrame, "to_parquet", lambda self, path, index=False: self.to_pickle(path))
    monkeypatch.setattr(pd, "read_parquet", lambda path: pd.read_pickle(path))
    store = PartitionStore(tmp_path)
    store.write_status("daily", "20260806", status="failed", metadata={"error": "temporary"})
    assert not store.is_terminal("daily", "20260806")
    store.write("daily", "20260806", pd.DataFrame({"x": [1]}), status="success")
    assert store.is_terminal("daily", "20260806")
    assert store.read_manifest("daily", "20260806")["sha256"]
