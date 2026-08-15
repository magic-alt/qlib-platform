from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from tushare_qlib.settings import Paths, Settings
from tushare_qlib.train_select import (
    _align_oos_labels,
    _default_splits_from_data,
    _export_daily_selections,
    _export_daily_signal_scores,
    _load_local_benchmark_series,
    _official_calendar,
    _promotion_authorized,
    _research_label_horizon_days,
)


def test_promotion_authorization_is_explicitly_false_outside_release_mode():
    assert _promotion_authorized("component", promoted=True) is False
    assert _promotion_authorized("holdout", promoted=True) is False
    assert _promotion_authorized("release", promoted=False) is False
    assert _promotion_authorized("release", promoted=True) is True


def test_align_oos_labels_reindexes_label_superset_to_predictions():
    prediction_index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2026-01-05"), "A"),
            (pd.Timestamp("2026-01-06"), "B"),
        ],
        names=["datetime", "instrument"],
    )
    label_index = prediction_index.append(
        pd.MultiIndex.from_tuples(
            [(pd.Timestamp("2026-01-06"), "C")],
            names=["datetime", "instrument"],
        )
    )
    predictions = pd.DataFrame({"score": [0.3, 0.2]}, index=prediction_index)
    labels = pd.DataFrame({"target": [0.01, float("nan"), 0.02]}, index=label_index)

    aligned = _align_oos_labels(predictions, labels)

    assert aligned.index.equals(predictions.index)
    assert list(aligned.columns) == ["label"]
    assert aligned.iloc[0, 0] == pytest.approx(0.01)
    assert pd.isna(aligned.iloc[1, 0])


def test_align_oos_labels_rejects_prediction_without_label_key():
    prediction_index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-01-05"), "A")],
        names=["datetime", "instrument"],
    )
    label_index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-01-05"), "B")],
        names=["datetime", "instrument"],
    )

    with pytest.raises(ValueError, match="1 rows without labels"):
        _align_oos_labels(
            pd.DataFrame({"score": [0.3]}, index=prediction_index),
            pd.DataFrame({"label": [0.01]}, index=label_index),
        )


def test_default_splits_reserve_release_observations_and_future_trade_day(tmp_path, monkeypatch):
    dates = pd.bdate_range("2023-01-02", periods=732)
    paths = Paths.from_root(tmp_path / "data")
    qlib_data = tmp_path / "qlib"
    (qlib_data / "calendars").mkdir(parents=True)
    (qlib_data / "calendars" / "day.txt").write_text("\n".join(dates.strftime("%Y-%m-%d")), encoding="utf-8")
    paths.metadata.mkdir(parents=True)
    pd.DataFrame({"cal_date": dates, "is_open": 1}).to_parquet(
        paths.metadata / "trade_calendar.parquet", index=False
    )
    settings = Settings(
        config_path=tmp_path / "configs" / "pipeline.yaml",
        data={
            "research": {
                "min_history_days": 700,
                "label_horizon_days": 5,
                "promotion_thresholds": {"min_observations": 252},
            }
        },
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=qlib_data,
    )
    monkeypatch.setattr(
        "tushare_qlib.research_timing.PartitionStore",
        lambda root: SimpleNamespace(list_dates=lambda dataset: dates.strftime("%Y%m%d").tolist()),
    )

    train, valid, test = _default_splits_from_data(settings)

    assert train == (dates[0].strftime("%Y-%m-%d"), dates[353].strftime("%Y-%m-%d"))
    assert valid == (dates[354].strftime("%Y-%m-%d"), dates[472].strftime("%Y-%m-%d"))
    assert test == (dates[473].strftime("%Y-%m-%d"), dates[-2].strftime("%Y-%m-%d"))
    assert len(dates[(dates >= test[0]) & (dates <= test[1])]) == 258


def test_research_label_horizon_defaults_to_strategy_hold_threshold(tmp_path):
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={"strategy": {"topk_dropout": {"hold_thresh": 7}}},
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )

    assert _research_label_horizon_days(settings) == 7


def test_platform_benchmark_is_loaded_from_bound_release(tmp_path, monkeypatch):
    benchmark_path = tmp_path / "benchmark.parquet"
    pd.DataFrame(
        {
            "ts_code": ["000300.SH", "000300.SH", "000905.SH"],
            "trade_date": ["2024-01-02", "2024-01-03", "2024-01-02"],
            "close": [100.0, 102.0, 200.0],
        }
    ).to_parquet(benchmark_path, index=False)
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={"data_source": {"kind": "platform_release"}},
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )
    release = SimpleNamespace(
        data_release_id="ds_test",
        files=lambda role: [benchmark_path] if role == "benchmark" else [],
    )
    monkeypatch.setattr("tushare_qlib.platform_release.load_platform_release", lambda value: release)

    actual = _load_local_benchmark_series(
        settings, "SH000300", pd.DatetimeIndex(["2024-01-02", "2024-01-03"])
    )

    assert actual.tolist() == pytest.approx([0.0, 0.02])


def test_platform_official_calendar_is_loaded_from_bound_release(tmp_path, monkeypatch):
    calendar_path = tmp_path / "calendar.parquet"
    pd.DataFrame({"cal_date": ["2024-01-01", "2024-01-02", "2024-01-03"], "is_open": [0, 1, 1]}).to_parquet(
        calendar_path, index=False
    )
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={"data_source": {"kind": "platform_release"}},
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )
    release = SimpleNamespace(
        data_release_id="ds_test",
        files=lambda role: [calendar_path] if role == "trading_calendar" else [],
    )
    monkeypatch.setattr("tushare_qlib.platform_release.load_platform_release", lambda value: release)

    assert _official_calendar(settings).tolist() == [
        pd.Timestamp("2024-01-02"),
        pd.Timestamp("2024-01-03"),
    ]


def test_default_splits_fail_fast_when_qlib_calendar_is_stale(tmp_path, monkeypatch):
    raw_dates = pd.bdate_range("2023-01-02", periods=732)
    qlib_dates = raw_dates[:384]
    paths = Paths.from_root(tmp_path / "data")
    qlib_data = tmp_path / "qlib"
    (qlib_data / "calendars").mkdir(parents=True)
    (qlib_data / "calendars" / "day.txt").write_text(
        "\n".join(qlib_dates.strftime("%Y-%m-%d")), encoding="utf-8"
    )
    paths.metadata.mkdir(parents=True)
    pd.DataFrame({"cal_date": raw_dates, "is_open": 1}).to_parquet(
        paths.metadata / "trade_calendar.parquet", index=False
    )
    settings = Settings(
        config_path=tmp_path / "configs" / "pipeline.yaml",
        data={"research": {"min_history_days": 700}},
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=qlib_data,
    )
    monkeypatch.setattr(
        "tushare_qlib.research_timing.PartitionStore",
        lambda root: SimpleNamespace(list_dates=lambda dataset: raw_dates.strftime("%Y%m%d").tolist()),
    )

    with pytest.raises(ValueError, match="shared raw/Qlib trading days; detected 384"):
        _default_splits_from_data(settings)


def test_default_splits_fail_fast_when_official_calendar_is_stale(tmp_path, monkeypatch):
    dates = pd.bdate_range("2023-01-02", periods=732)
    paths = Paths.from_root(tmp_path / "data")
    qlib_data = tmp_path / "qlib"
    (qlib_data / "calendars").mkdir(parents=True)
    (qlib_data / "calendars" / "day.txt").write_text("\n".join(dates.strftime("%Y-%m-%d")), encoding="utf-8")
    paths.metadata.mkdir(parents=True)
    pd.DataFrame({"cal_date": dates[:-5], "is_open": 1}).to_parquet(
        paths.metadata / "trade_calendar.parquet", index=False
    )
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={"research": {"min_history_days": 700}},
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=qlib_data,
    )
    monkeypatch.setattr(
        "tushare_qlib.research_timing.PartitionStore",
        lambda root: SimpleNamespace(list_dates=lambda dataset: dates.strftime("%Y%m%d").tolist()),
    )

    with pytest.raises(ValueError, match="official trading calendar is stale"):
        _default_splits_from_data(settings)


def test_export_daily_selections_writes_one_topn_file_per_signal_date(tmp_path, monkeypatch):
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    pd.DataFrame(
        {
            "cal_date": pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]),
            "is_open": [1, 1, 1],
        }
    ).to_parquet(paths.metadata / "trade_calendar.parquet")
    settings = Settings(
        config_path=tmp_path / "configs" / "pipeline.yaml",
        data={"data_source": {"kind": "tushare"}},
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )
    index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2026-01-05"), "SH600000"),
            (pd.Timestamp("2026-01-05"), "SZ000001"),
            (pd.Timestamp("2026-01-05"), "SH600519"),
            (pd.Timestamp("2026-01-06"), "SH600000"),
            (pd.Timestamp("2026-01-06"), "SZ000001"),
            (pd.Timestamp("2026-01-06"), "SH600519"),
        ],
        names=["datetime", "instrument"],
    )
    score = pd.Series([0.1, 0.3, 0.2, 0.5, 0.2, 0.4], index=index)
    monkeypatch.setattr(
        "tushare_qlib.train_select._selection_volatility_by_date",
        lambda selections: {
            date: pd.Series({instrument: 0.02 for instrument in selected.index})
            for date, selected in selections.items()
        },
    )

    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    latest_path, latest = _export_daily_selections(
        settings,
        score,
        model_id="model-1",
        topn=2,
        lineage_id="lineage-1",
        manifest_path=manifest,
    )

    first = pd.read_csv(paths.output / "selection_20260105.csv")
    assert latest_path == paths.output / "selection_20260106.csv"
    assert first["instrument"].tolist() == ["SZ000001", "SH600519"]
    assert latest["instrument"].tolist() == ["SH600000", "SH600519"]
    assert set(first["trade_date"]) == {"2026-01-06"}
    assert set(latest["trade_date"]) == {"2026-01-07"}
    assert first["signal_id"].iloc[0] != latest["signal_id"].iloc[0]
    assert (first["target_weight"] == 0.5).all()
    assert first["score_rank"].tolist() == [1, 2]
    assert first["is_model_topk"].all()

    signal_paths = _export_daily_signal_scores(
        settings,
        score,
        model_id="model-1",
        lineage_id="lineage-1",
        manifest_path=manifest,
    )
    full = pd.read_parquet(signal_paths[pd.Timestamp("2026-01-05")])
    assert full["instrument"].tolist() == ["SZ000001", "SH600519", "SH600000"]
    assert full["score_rank"].tolist() == [1, 2, 3]
    assert set(full["strategy_topk"]) == {30}
