from __future__ import annotations

import pandas as pd

from tushare_qlib.feature_store import load_feature_store, materialize_feature_store
from tushare_qlib.settings import Paths, Settings


def test_feature_store_partitions_and_reuses_raw_features(tmp_path, monkeypatch):
    paths = Paths.from_root(tmp_path / "data")
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={
            "research": {"feature_store": {"enabled": True}, "label_horizon_days": 5},
            "universe": {"instruments": "all"},
        },
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )
    index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2023-12-29"), "SH600000"),
            (pd.Timestamp("2024-01-02"), "SH600000"),
        ],
        names=["datetime", "instrument"],
    )
    columns = pd.MultiIndex.from_tuples([("feature", "A"), ("label", "LABEL0")])
    source = pd.DataFrame([[1.0, 0.1], [2.0, 0.2]], index=index, columns=columns)
    calls = []
    monkeypatch.setattr(
        "tushare_qlib.feature_store._raw_features",
        lambda *args: calls.append(args) or source,
    )

    first = materialize_feature_store(settings, "2023-12-29", "2024-01-02")
    second = materialize_feature_store(settings, "2023-12-29", "2024-01-02")
    loaded = load_feature_store(first, "2023-12-29", "2024-01-02")

    assert first == second
    assert len(calls) == 1
    assert sorted(path.name for path in first.glob("year=*.parquet")) == [
        "year=2023.parquet",
        "year=2024.parquet",
    ]
    pd.testing.assert_frame_equal(loaded, source, check_freq=False)
