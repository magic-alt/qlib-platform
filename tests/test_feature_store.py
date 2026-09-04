from __future__ import annotations

from pathlib import Path

import pandas as pd

from tushare_qlib.feature_store import (
    FEATURE_LOADER_CONTRACT,
    _contract,
    _raw_features,
    load_feature_store,
    materialize_feature_store,
    prepare_feature_data,
)
from tushare_qlib.settings import Paths, Settings


def _settings(tmp_path, *, feature_store: dict[str, object] | None = None) -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    return Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={
            "research": {"feature_store": {"enabled": True, **(feature_store or {})}},
            "universe": {"instruments": "all"},
        },
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )


def test_feature_store_partitions_and_reuses_raw_features(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
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
    monkeypatch.setattr("tushare_qlib.feature_store._initialize_qlib", lambda settings: None)
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
    settings = _settings(tmp_path)
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2024-01-02"), "SH600000")],
        names=["datetime", "instrument"],
    )
    source = pd.DataFrame(
        [[1.0]], index=index, columns=pd.MultiIndex.from_tuples([("feature", "A")])
    )
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr("tushare_qlib.feature_store._initialize_qlib", lambda settings: None)
    monkeypatch.setattr(
        "tushare_qlib.feature_store._raw_features",
        lambda *args: calls.append(args) or source,
    )

    _, cold = prepare_feature_data(settings, "2024-01-02", "2024-01-02")
    _, warm = prepare_feature_data(settings, "2024-01-02", "2024-01-02")

    assert len(calls) == 1
    assert cold["cacheStatus"] == "MATERIALIZED"
    assert cold["rawMaterializationCalls"] == 1
    assert cold["featureSemanticId"].startswith("frs_")
    assert warm["cacheStatus"] == "REUSED"
    assert warm["rawMaterializationCalls"] == 0


def test_raw_feature_contract_excludes_cache_and_fitted_processor_implementation(tmp_path):
    settings = _settings(tmp_path)

    contract = _contract(settings, "2024-01-02", "2024-01-31")

    assert contract["featureLoaderContract"] == FEATURE_LOADER_CONTRACT
    implementation = contract["implementationSha256"]
    assert isinstance(implementation, dict)
    assert "custom_handler.py" in implementation
    assert "feature_store.py" not in implementation
    assert "processors.py" not in implementation


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
    expected = pd.DataFrame(
        [[1.0]], index=index, columns=pd.MultiIndex.from_tuples([("feature", "A")])
    )
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


def test_feature_store_extends_same_dataset_without_full_recompute(tmp_path, monkeypatch):
    settings = _settings(tmp_path, feature_store={"append_lookback_trading_days": 60})
    columns = pd.MultiIndex.from_tuples([("feature", "A")])
    first_index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2026-01-02"), "SH600000"),
            (pd.Timestamp("2026-01-03"), "SH600000"),
        ],
        names=["datetime", "instrument"],
    )
    extension_index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2026-01-03"), "SH600000"),
            (pd.Timestamp("2026-01-04"), "SH600000"),
        ],
        names=["datetime", "instrument"],
    )
    frames = [
        pd.DataFrame([[1.0], [2.0]], index=first_index, columns=columns),
        pd.DataFrame([[20.0], [3.0]], index=extension_index, columns=columns),
    ]
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr("tushare_qlib.feature_store._initialize_qlib", lambda settings: None)
    monkeypatch.setattr(
        "tushare_qlib.feature_store._raw_features",
        lambda *args, **kwargs: calls.append((*args, kwargs)) or frames[len(calls) - 1],
    )
    monkeypatch.setattr(
        "tushare_qlib.feature_store._lookback_start",
        lambda value, trading_days: "2026-01-03",
    )
    monkeypatch.setattr(
        "tushare_qlib.feature_store._dataset_snapshot",
        lambda settings: {
            "sha256": "same",
            "versionId": "same",
            "manifestSha256": "manifest-same",
            "mode": "full",
            "syncContext": None,
            "lastDate": "2026-01-04",
            "datasetId": "test",
            "fields": ["close"],
            "parents": [],
        },
    )

    materialize_feature_store(settings, "2026-01-02", "2026-01-03")
    loaded, evidence = prepare_feature_data(settings, "2026-01-02", "2026-01-04")

    assert len(calls) == 2
    assert calls[1][1:3] == ("2026-01-03", "2026-01-04")
    assert evidence["cacheStatus"] == "EXTENDED"
    assert evidence["rawMaterializationCalls"] == 1
    assert evidence["recomputeStartTime"] == "2026-01-03"
    assert loaded.loc[(pd.Timestamp("2026-01-02"), "SH600000")].iloc[0] == 1.0
    assert loaded.loc[(pd.Timestamp("2026-01-03"), "SH600000")].iloc[0] == 20.0
    assert loaded.loc[(pd.Timestamp("2026-01-04"), "SH600000")].iloc[0] == 3.0


def test_feature_store_incrementally_refreshes_changed_tail_from_direct_parent(tmp_path, monkeypatch):
    settings = _settings(tmp_path, feature_store={"append_lookback_trading_days": 60})
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
            (pd.Timestamp("2026-01-03"), "SH600000"),
            (pd.Timestamp("2026-01-04"), "SH600000"),
        ],
        names=["datetime", "instrument"],
    )
    frames = [
        pd.DataFrame([[1.0, 0.1], [2.0, 0.2]], index=original_index, columns=columns),
        pd.DataFrame([[20.0, 0.2], [3.0, 0.3]], index=refresh_index, columns=columns),
    ]
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr("tushare_qlib.feature_store._initialize_qlib", lambda settings: None)
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
                "versionId": "old",
                "manifestSha256": "manifest-old",
                "mode": "full",
                "syncContext": None,
                "lastDate": "2026-01-03",
                "datasetId": "test",
                "fields": ["close"],
                "parents": [],
            }
        return {
            "sha256": "new",
            "versionId": "new",
            "manifestSha256": "manifest-new",
            "mode": "update",
            "syncContext": {
                "changed_trade_dates": ["2026-01-04"],
                "revised_symbols": [],
            },
            "lastDate": "2026-01-04",
            "datasetId": "test",
            "fields": ["close"],
            "parents": [{"version_id": "old", "relation": "updated_from"}],
        }

    monkeypatch.setattr("tushare_qlib.feature_store._dataset_snapshot", snapshot)
    store = materialize_feature_store(settings, "2026-01-02", "2026-01-03")
    state["snapshot"] = "new"

    loaded, evidence = prepare_feature_data(settings, "2026-01-02", "2026-01-04")
    updated = Path(str(evidence["path"]))

    assert updated != store
    assert len(calls) == 2
    assert calls[1][1:3] == ("2026-01-03", "2026-01-04")
    assert evidence["cacheStatus"] == "INCREMENTAL"
    assert evidence["rawMaterializationCalls"] == 1
    assert evidence["sourceDatasetVersionId"] == "old"
    assert evidence["recomputeStartTime"] == "2026-01-03"
    original = load_feature_store(store, "2026-01-02", "2026-01-03")
    assert original.loc[(pd.Timestamp("2026-01-03"), "SH600000")].iloc[0] == 2.0
    assert loaded.loc[(pd.Timestamp("2026-01-03"), "SH600000")].iloc[0] == 20.0
    assert loaded.loc[(pd.Timestamp("2026-01-04"), "SH600000")].iloc[0] == 3.0


def test_feature_store_rebinds_direct_parent_when_changes_are_after_requested_range(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-01-02"), "SH600000"), (pd.Timestamp("2026-01-03"), "SH600000")],
        names=["datetime", "instrument"],
    )
    source = pd.DataFrame(
        [[1.0], [2.0]], index=index, columns=pd.MultiIndex.from_tuples([("feature", "A")])
    )
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr("tushare_qlib.feature_store._initialize_qlib", lambda settings: None)
    monkeypatch.setattr(
        "tushare_qlib.feature_store._raw_features",
        lambda *args: calls.append(args) or source,
    )
    state = {"snapshot": "old"}

    def snapshot(_settings):
        if state["snapshot"] == "old":
            return {
                "sha256": "old",
                "versionId": "old",
                "manifestSha256": "manifest-old",
                "syncContext": None,
                "datasetId": "test",
                "fields": ["close"],
                "parents": [],
            }
        return {
            "sha256": "new",
            "versionId": "new",
            "manifestSha256": "manifest-new",
            "syncContext": {"changed_trade_dates": ["2026-01-10"], "revised_symbols": []},
            "datasetId": "test",
            "fields": ["close"],
            "parents": [{"version_id": "old", "relation": "updated_from"}],
        }

    monkeypatch.setattr("tushare_qlib.feature_store._dataset_snapshot", snapshot)
    old_path = materialize_feature_store(settings, "2026-01-02", "2026-01-03")
    state["snapshot"] = "new"

    _, evidence = prepare_feature_data(settings, "2026-01-02", "2026-01-03")

    assert len(calls) == 1
    assert evidence["cacheStatus"] == "REBOUND"
    assert evidence["rawMaterializationCalls"] == 0
    assert evidence["sourceDatasetVersionId"] == "old"
    assert evidence["sourceFeatureSnapshotId"] == old_path.name
    assert Path(str(evidence["path"])) != old_path


def test_cross_version_cache_without_direct_parent_fails_closed(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-01-02"), "SH600000"), (pd.Timestamp("2026-01-03"), "SH600000")],
        names=["datetime", "instrument"],
    )
    old = pd.DataFrame(
        [[1.0], [2.0]], index=index, columns=pd.MultiIndex.from_tuples([("feature", "A")])
    )
    new = pd.DataFrame(
        [[10.0], [20.0]], index=index, columns=pd.MultiIndex.from_tuples([("feature", "A")])
    )
    frames = [old, new]
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr("tushare_qlib.feature_store._initialize_qlib", lambda settings: None)
    monkeypatch.setattr(
        "tushare_qlib.feature_store._raw_features",
        lambda *args: calls.append(args) or frames[len(calls) - 1],
    )
    state = {"snapshot": "old"}

    def snapshot(_settings):
        if state["snapshot"] == "old":
            return {
                "sha256": "old",
                "versionId": "old",
                "manifestSha256": "manifest-old",
                "syncContext": None,
                "datasetId": "test",
                "fields": ["close"],
                "parents": [],
            }
        return {
            "sha256": "new",
            "versionId": "new",
            "manifestSha256": "manifest-new",
            "syncContext": {"changed_trade_dates": ["2026-01-10"], "revised_symbols": []},
            "datasetId": "test",
            "fields": ["close"],
            "parents": [],
        }

    monkeypatch.setattr("tushare_qlib.feature_store._dataset_snapshot", snapshot)
    materialize_feature_store(settings, "2026-01-02", "2026-01-03")
    state["snapshot"] = "new"

    loaded, evidence = prepare_feature_data(settings, "2026-01-02", "2026-01-03")

    assert len(calls) == 2
    assert evidence["cacheStatus"] == "MATERIALIZED"
    assert evidence["rawMaterializationCalls"] == 1
    assert loaded.loc[(pd.Timestamp("2026-01-02"), "SH600000")].iloc[0] == 10.0


def test_feature_store_revised_symbols_fail_closed_to_full_materialization(tmp_path, monkeypatch):
    settings = _settings(tmp_path, feature_store={"append_lookback_trading_days": 60})
    columns = pd.MultiIndex.from_tuples([("feature", "A")])
    first_index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-01-02"), "SH600000"), (pd.Timestamp("2026-01-03"), "SH600000")],
        names=["datetime", "instrument"],
    )
    second_index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2026-01-02"), "SH600000"),
            (pd.Timestamp("2026-01-03"), "SH600000"),
            (pd.Timestamp("2026-01-04"), "SH600000"),
        ],
        names=["datetime", "instrument"],
    )
    frames = [
        pd.DataFrame([[1.0], [2.0]], index=first_index, columns=columns),
        pd.DataFrame([[10.0], [20.0], [3.0]], index=second_index, columns=columns),
    ]
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr("tushare_qlib.feature_store._initialize_qlib", lambda settings: None)
    monkeypatch.setattr(
        "tushare_qlib.feature_store._raw_features",
        lambda *args: calls.append(args) or frames[len(calls) - 1],
    )
    state = {"snapshot": "old"}

    def snapshot(_settings):
        if state["snapshot"] == "old":
            return {
                "sha256": "old",
                "versionId": "old",
                "manifestSha256": "manifest-old",
                "syncContext": None,
                "datasetId": "test",
                "fields": ["close"],
                "parents": [],
            }
        return {
            "sha256": "new",
            "versionId": "new",
            "manifestSha256": "manifest-new",
            "syncContext": {
                "changed_trade_dates": ["2026-01-04"],
                "revised_symbols": ["SH600000"],
            },
            "datasetId": "test",
            "fields": ["close"],
            "parents": [{"version_id": "old", "relation": "updated_from"}],
        }

    monkeypatch.setattr("tushare_qlib.feature_store._dataset_snapshot", snapshot)
    materialize_feature_store(settings, "2026-01-02", "2026-01-03")
    state["snapshot"] = "new"

    _, evidence = prepare_feature_data(settings, "2026-01-02", "2026-01-04")

    assert len(calls) == 2
    assert calls[1][1:3] == ("2026-01-02", "2026-01-04")
    assert evidence["cacheStatus"] == "MATERIALIZED"
    assert evidence["rawMaterializationCalls"] == 1
