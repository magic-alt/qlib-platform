from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from tushare_qlib.production_replay import _snapshot_for_date


def test_replay_requires_point_in_time_snapshot_matching_signal_date(tmp_path: Path):
    root = tmp_path / "snapshots"
    snapshot = root / "20260810"
    snapshot.mkdir(parents=True)
    (snapshot / "dataset_manifest.json").write_text(
        json.dumps({"smoke_test": {"last_date": "2026-08-10 00:00:00"}}), encoding="utf-8"
    )

    assert _snapshot_for_date(root, "2026-08-10") == snapshot

    with pytest.raises(FileNotFoundError, match="snapshot is missing"):
        _snapshot_for_date(root, "2026-08-11")

    (snapshot / "dataset_manifest.json").write_text(
        json.dumps({"smoke_test": {"last_date": pd.Timestamp("2026-08-09").isoformat()}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="last date does not match"):
        _snapshot_for_date(root, "2026-08-10")
