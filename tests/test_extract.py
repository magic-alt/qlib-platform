from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from tushare_qlib.extract import _write_parquet_atomic


def test_atomic_metadata_write_preserves_hard_linked_snapshot(tmp_path: Path):
    current = tmp_path / "current" / "trade_calendar.parquet"
    snapshot = tmp_path / "versions" / "v1" / "trade_calendar.parquet"
    _write_parquet_atomic(pd.DataFrame({"is_open": [1]}), current)
    snapshot.parent.mkdir(parents=True)
    os.link(current, snapshot)

    _write_parquet_atomic(pd.DataFrame({"is_open": [0]}), current)

    assert not os.path.samefile(current, snapshot)
    assert pd.read_parquet(current)["is_open"].tolist() == [0]
    assert pd.read_parquet(snapshot)["is_open"].tolist() == [1]
