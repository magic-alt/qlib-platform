from __future__ import annotations

import pandas as pd

from tushare_qlib.feature_store import (
    _raw_features,
    load_feature_store,
    materialize_feature_store,
    prepare_feature_data,
)
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
    pd.testing.assert_frame_equal(loaded, source.drop(columns="label", level=0), check_freq=False)


def test_prepare_feature_data_reports_materialization_and_reuse(tmp_path, monkeypatch):
    paths = Paths.from_root(tmp_path / "data")
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={
            "research": {"feature_store": {"enabled": True}},
            "universe": {"instruments": "all"},
        },
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2024-01-02"), "SH600000")],
        names=["datetime", "instrument"],
    )
    source = pd.DataFrame([[1.0]], index=index, columns=pd.MultiIndex.from_tuples([("feature", "A")]))
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        "tushare_qlib.feature_store._raw_features",
        lambda *args: calls.append(args) or source,
    )

    _, cold = prepare_feature_data(settings, "2024-01-02", "2024-01-02")
    _, warm = prepare_feature_data(settings, "2024-01-02", "2024-01-02")

    assert len(calls) == 1
    assert cold["cacheStatus"] == "MATERIALIZED"
    assert cold["rawMaterializationCalls"] == 1
    assert warm["cacheStatus"] == "REUSED"
    assert warm["rawMaterializationCalls"] == 0


def test_raw_features_uses_feature_only_loader(tmp_path, monkeypatch):
    paths = Paths.from_root(tmp_path / "data")
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={"universe": {"instruments": "csi300"}},
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2024-01-02"), "SH600000")],
        names=["datetime", "instrument"],
    )
    expected = pd.DataFrame([[1.0]], index=index, columns=pd.MultiIndex.from_tuples([("feature", "A")]))
    observed: dict[str, object] = {}

    class FakeHandler:
        def __init__(self, **kwargs):
            self.instruments = kwargs["instruments"]

        def get_feature_config(self):
            return ["$close"], ["CLOSE"]

    class FakeLoader:
        def __init__(self, config):
            observed["config"] = config

        def load(self, instruments, *, start_time, end_time):
            observed["load"] = (instruments, start_time, end_time)
            return expected

    monkeypatch.setattr("tushare_qlib.feature_store.alpha_pack_from_settings", lambda settings: object())
    monkeypatch.setattr("tushare_qlib.feature_store.assert_alpha_pack_compatible", lambda *args: None)
    monkeypatch.setattr("tushare_qlib.feature_store.handler_class", lambda pack: FakeHandler)
    monkeypatch.setattr("qlib.data.dataset.loader.QlibDataLoader", FakeLoader)

    actual = _raw_features(settings, "2024-01-02", "2024-01-02")

    assert observed["config"] == {"feature": (["$close"], ["CLOSE"])}
    assert observed["load"] == ("csi300", "2024-01-02", "2024-01-02")
    pd.testing.assert_frame_equal(actual, expected)


def test_feature_store_incrementally_refreshes_changed_tail(tmp_path, monkeypatch):
    paths = Paths.from_root(tmp_path / "data")
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={
            "research": {
                "feature_store": {
                    "enabled": True,
                    "append_lookback_trading_days": 60,
                }
            },
            "universe": {"instruments": "all"},
        },
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )
    columns = pd.MultiIndex.from_tuples([("feature", "A"), ("label", "LABEL0")])
    original_index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2026-01-02"), "SH600000"),
            (pd.Timestamp("2026-01-03"), "SH600000"),
        ],
        names=["datetime", "instrument"],
    )
    refresh_index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2026-01-02"), "SH600000"),
            (pd.Timestamp("2026-01-03"), "SH600000"),
            (pd.Timestamp("2026-01-04"), "SH600000"),
        ],
        names=["datetime", "instrument"],
    )
    frames = [
        pd.DataFrame([[1.0, 0.1], [2.0, 0.2]], index=original_index, columns=columns),
        pd.DataFrame([[1.0, 0.1], [20.0, 0.2], [3.0, 0.3]], index=refresh_index, columns=columns),
    ]
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        "tushare_qlib.feature_store._raw_features",
        lambda *args, **kwargs: calls.append((*args, kwargs)) or frames[len(calls) - 1],
    )
    monkeypatch.setattr(
        "tushare_qlib.feature_store._lookback_start",
        lambda value, trading_days: "2026-01-03",
    )
    state = {"snapshot": "old"}

    def snapshot(_settings):
        if state["snapshot"] == "old":
            return {
                "sha256": "old",
                "mode": "full",
                "syncContext": None,
                "lastDate": "2026-01-03",
                "datasetId": "test",
                "fields": ["close"],
            }
        return {
            "sha256": "new",
            "mode": "update",
            "syncContext": {
                "changed_trade_dates": ["2026-01-04"],
                "revised_symbols": [],
            },
            "lastDate": "2026-01-04",
            "datasetId": "test",
            "fields": ["close"],
        }

    monkeypatch.setattr("tushare_qlib.feature_store._dataset_snapshot", snapshot)
    store = materialize_feature_store(settings, "2026-01-02", "2026-01-03")
    state["snapshot"] = "new"

    updated = materialize_feature_store(settings, "2026-01-02", "2026-01-04")
    loaded = load_feature_store(updated, "2026-01-02", "2026-01-04")

    assert updated != store
    assert len(calls) == 2
    original = load_feature_store(store, "2026-01-02", "2026-01-03")
    assert original.loc[(pd.Timestamp("2026-01-03"), "SH600000")].iloc[0] == 2.0
    assert loaded.loc[(pd.Timestamp("2026-01-03"), "SH600000")].iloc[0] == 20.0
    assert loaded.loc[(pd.Timestamp("2026-01-04"), "SH600000")].iloc[0] == 3.0
