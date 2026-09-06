from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from qlib_platform.research.workflow import train_select


def _settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        paths=SimpleNamespace(
            state=tmp_path / "state",
            models=tmp_path / "models",
            metadata=tmp_path / "metadata",
        ),
        data={"research": {}},
        config_path=tmp_path / "config.yaml",
        uses_platform_release=lambda: False,
    )


def _multi_index() -> pd.MultiIndex:
    return pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-09-01"), "SZ000001"), (pd.Timestamp("2026-09-02"), "SZ000001")],
        names=["datetime", "instrument"],
    )


def test_mlflow_tracking_configuration_and_helpers(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    monkeypatch.delenv("MLFLOW_DEFAULT_ARTIFACT_ROOT", raising=False)
    train_select._configure_mlflow_tracking(settings)
    assert os.environ["MLFLOW_TRACKING_URI"].startswith("sqlite:///")
    assert Path(os.environ["MLFLOW_DEFAULT_ARTIFACT_ROOT"]).parts[-2:] == ("models", "mlruns")
    assert train_select._sqlite_tracking_uri(tmp_path / "x.db").startswith("sqlite:///")
    assert train_select._promotion_authorized("release", True)
    assert not train_select._promotion_authorized("release", False)
    assert not train_select._promotion_authorized("research", True)


def test_align_oos_labels_accepts_series_and_single_column_frame() -> None:
    idx = _multi_index()
    predictions = pd.Series([0.1, 0.2], index=idx)
    labels = pd.Series([1.0, -1.0], index=idx, name="future")
    aligned = train_select._align_oos_labels(predictions, labels)
    assert aligned.index.equals(idx)
    assert aligned["label"].tolist() == [1.0, -1.0]
    frame = pd.DataFrame({"target": [1.0, -1.0]}, index=idx)
    assert train_select._align_oos_labels(predictions, frame).columns.tolist() == ["label"]


def test_align_oos_labels_fails_closed_for_schema_duplicates_and_missing() -> None:
    idx = _multi_index()
    predictions = pd.Series([0.1, 0.2], index=idx)
    labels = pd.Series([1.0, -1.0], index=idx)
    with pytest.raises(ValueError, match="exactly one"):
        train_select._align_oos_labels(predictions, pd.DataFrame({"a": [1, 2], "b": [3, 4]}, index=idx))
    with pytest.raises(ValueError, match="predictions require"):
        train_select._align_oos_labels(pd.Series([1.0, 2.0]), labels)
    with pytest.raises(ValueError, match="labels require"):
        train_select._align_oos_labels(predictions, pd.Series([1.0, 2.0]))
    dup = pd.MultiIndex.from_tuples([idx[0], idx[0]], names=idx.names)
    with pytest.raises(ValueError, match="predictions contain duplicate"):
        train_select._align_oos_labels(pd.Series([1.0, 2.0], index=dup), labels)
    with pytest.raises(ValueError, match="labels contain duplicate"):
        train_select._align_oos_labels(predictions, pd.Series([1.0, 2.0], index=dup))
    with pytest.raises(ValueError, match="without labels"):
        train_select._align_oos_labels(predictions, labels.iloc[:1])


def test_official_calendar_local_and_next_trade_date(tmp_path) -> None:
    settings = _settings(tmp_path)
    settings.paths.metadata.mkdir(parents=True)
    path = settings.paths.metadata / "trade_calendar.parquet"
    pd.DataFrame({"cal_date": ["2026-09-01", "2026-09-02", "2026-09-03"], "is_open": [1, 0, 1]}).to_parquet(
        path, index=False
    )
    calendar = train_select._official_calendar(settings)
    assert calendar.tolist() == [pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-03")]
    assert train_select._next_trade_date(settings, "2026-09-01") == "2026-09-03"
    with pytest.raises(ValueError, match="no open day"):
        train_select._next_trade_date(settings, "2026-09-03")
    path.unlink()
    with pytest.raises(FileNotFoundError, match="official trading calendar"):
        train_select._official_calendar(settings)
    pd.DataFrame({"cal_date": ["2026-09-01"]}).to_parquet(path, index=False)
    with pytest.raises(ValueError, match="missing columns"):
        train_select._official_calendar(settings)
    pd.DataFrame({"cal_date": ["2026-09-01"], "is_open": [0]}).to_parquet(path, index=False)
    with pytest.raises(ValueError, match="no open dates"):
        train_select._official_calendar(settings)


def test_stock_and_index_code_normalization() -> None:
    assert train_select._normalize_stock_code_for_qlib("000001.sz") == "SZ000001"
    assert train_select._normalize_stock_code_for_qlib("SH600000") == "SH600000"
    assert train_select._to_tushare_index_code("SH000300") == "000300.SH"
    assert train_select._to_tushare_index_code("000300.SH") == "000300.SH"
    assert train_select._to_tushare_index_code("not-a-code") is None


def test_load_local_benchmark_series_validates_and_computes_returns(tmp_path) -> None:
    settings = _settings(tmp_path)
    bench_dir = settings.paths.metadata / "benchmarks"
    bench_dir.mkdir(parents=True)
    path = bench_dir / "SH000300.parquet"
    calendar = pd.DatetimeIndex(pd.to_datetime(["2026-09-01", "2026-09-02", "2026-09-03"]))
    pd.DataFrame(
        {"trade_date": ["20260901", "20260902", "20260903"], "close": [100.0, 101.0, 99.0]}
    ).to_parquet(path, index=False)
    result = train_select._load_local_benchmark_series(settings, "SH000300", calendar)
    assert result.iloc[0] == pytest.approx(0.0)
    assert result.iloc[1] == pytest.approx(0.01)
    path.unlink()
    with pytest.raises(FileNotFoundError, match="Local benchmark"):
        train_select._load_local_benchmark_series(settings, "SH000300", calendar)
    pd.DataFrame({"trade_date": ["20260901"]}).to_parquet(path, index=False)
    with pytest.raises(ValueError, match="missing columns"):
        train_select._load_local_benchmark_series(settings, "SH000300", calendar)
    pd.DataFrame(columns=["trade_date", "close"]).to_parquet(path, index=False)
    with pytest.raises(ValueError, match="contains no rows"):
        train_select._load_local_benchmark_series(settings, "SH000300", calendar)
    pd.DataFrame({"trade_date": ["20260901", "20260901"], "close": [1.0, 2.0]}).to_parquet(path, index=False)
    with pytest.raises(ValueError, match="duplicate benchmark"):
        train_select._load_local_benchmark_series(settings, "SH000300", calendar)
    pd.DataFrame({"trade_date": ["20260901", "20260902"], "close": [1.0, 2.0]}).to_parquet(path, index=False)
    with pytest.raises(ValueError, match="does not cover"):
        train_select._load_local_benchmark_series(settings, "SH000300", calendar)


def test_default_splits_from_data_uses_thresholds_and_rejects_short_history(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    settings.data["research"] = {"min_history_days": 20, "promotion_thresholds": {"min_observations": 5}}
    dates = pd.date_range("2026-01-01", periods=40, freq="B")
    monkeypatch.setattr(train_select, "shared_research_calendar", lambda _: dates)
    monkeypatch.setattr(
        train_select,
        "label_timing_from_settings",
        lambda _: SimpleNamespace(lookahead_days=1, horizon_days=1),
    )
    train, valid, test = train_select._default_splits_from_data(settings)
    assert pd.Timestamp(train[0]) < pd.Timestamp(train[1]) < pd.Timestamp(valid[1]) < pd.Timestamp(test[0])
    settings.data["research"]["min_history_days"] = 100
    with pytest.raises(ValueError, match="at least 100"):
        train_select._default_splits_from_data(settings)


def test_research_label_horizon_delegates_timing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(train_select, "label_timing_from_settings", lambda _: SimpleNamespace(horizon_days=5))
    assert train_select._research_label_horizon_days(_settings(tmp_path)) == 5
