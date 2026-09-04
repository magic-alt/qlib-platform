from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qlib_platform.backtesting.prediction_backtest import _market_data_view
from qlib_platform.backtesting.topk_dropout import _normalise_quotes
from qlib_platform.models.model_runtime import StageTimings
from qlib_platform.settings import Paths, Settings


def _settings(tmp_path) -> Settings:
    return Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={},
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )


def _patch_dataset_calendar(monkeypatch, *dates: pd.Timestamp) -> None:
    monkeypatch.setattr(
        "qlib_platform.research.research_timing.shared_research_calendar",
        lambda settings: pd.DatetimeIndex(dates),
    )


def test_market_data_view_preserves_missing_qlib_limit_flags(tmp_path, monkeypatch):
    """Missing limit fields must reach the canonical normalizer unchanged.

    This reproduces the 2026-08-21 signal -> 2026-08-24 trade-date shape that
    previously caused strategy audit to report planned=[] while Qlib requested
    orders.  Qlib evaluates `$is_limit_* > 0`; NaN therefore is not positive
    limit evidence and must not be converted to 1.0 upstream.
    """

    signal_date = pd.Timestamp("2026-08-21")
    trade_date = pd.Timestamp("2026-08-24")
    instruments = ["SH600900", "SH600930"]
    score_index = pd.MultiIndex.from_product([[signal_date], instruments], names=["datetime", "instrument"])
    score = pd.Series([0.9, 0.1], index=score_index, name="score")
    quote_index = pd.MultiIndex.from_product([instruments, [trade_date]], names=["instrument", "datetime"])
    raw = pd.DataFrame(
        {
            "$close": [10.0, 11.0],
            "$is_limit_up": [np.nan, np.nan],
            "$is_limit_down": [np.nan, np.nan],
        },
        index=quote_index,
    )

    monkeypatch.setattr("qlib.data.D.features", lambda *args, **kwargs: raw.copy())
    _patch_dataset_calendar(monkeypatch, signal_date, trade_date)

    view = _market_data_view(
        _settings(tmp_path),
        score,
        StageTimings(),
        trade_dates=pd.DatetimeIndex([trade_date]),
    )
    quote = view.quote.set_index("instrument")

    assert set(quote.index) == set(instruments)
    assert quote["trade_date"].eq(trade_date).all()
    assert quote["paused"].eq(0.0).all()
    assert quote["is_limit_up"].isna().all()
    assert quote["is_limit_down"].isna().all()

    normalized = _normalise_quotes(view.quote.drop(columns=["trade_date"]), required=True)
    assert normalized["is_limit_up"].eq(0.0).all()
    assert normalized["is_limit_down"].eq(0.0).all()


def test_market_data_view_preserves_explicit_limit_evidence(tmp_path, monkeypatch):
    signal_date = pd.Timestamp("2026-08-21")
    trade_date = pd.Timestamp("2026-08-24")
    instruments = ["SH600900", "SH600930"]
    score_index = pd.MultiIndex.from_product([[signal_date], instruments], names=["datetime", "instrument"])
    score = pd.Series([0.9, 0.1], index=score_index, name="score")
    quote_index = pd.MultiIndex.from_product([instruments, [trade_date]], names=["instrument", "datetime"])
    raw = pd.DataFrame(
        {
            "$close": [10.0, 11.0],
            "$is_limit_up": [1.0, -1.0],
            "$is_limit_down": [0.0, 2.0],
        },
        index=quote_index,
    )

    monkeypatch.setattr("qlib.data.D.features", lambda *args, **kwargs: raw.copy())
    _patch_dataset_calendar(monkeypatch, signal_date, trade_date)

    view = _market_data_view(
        _settings(tmp_path),
        score,
        StageTimings(),
        trade_dates=pd.DatetimeIndex([trade_date]),
    )
    quote = view.quote.set_index("instrument")

    assert quote.at["SH600900", "is_limit_up"] == 1.0
    assert quote.at["SH600930", "is_limit_up"] == -1.0
    assert quote.at["SH600900", "is_limit_down"] == 0.0
    assert quote.at["SH600930", "is_limit_down"] == 2.0

    normalized = _normalise_quotes(view.quote.drop(columns=["trade_date"]), required=True)
    assert normalized.at["SH600900", "is_limit_up"] == 1.0
    assert normalized.at["SH600930", "is_limit_up"] == 0.0
    assert normalized.at["SH600930", "is_limit_down"] == 1.0


def test_market_data_view_uses_qlib_trade_timeline_when_metadata_calendar_lags(tmp_path, monkeypatch):
    signal_date = pd.Timestamp("2026-08-21")
    trade_date = pd.Timestamp("2026-08-24")
    instrument = "SH600900"
    settings = _settings(tmp_path)

    # Reproduce the partial-refactor failure: the mutable metadata calendar is
    # stale at the signal date while the pinned DatasetVersion/Qlib timeline has
    # the next trading day.  Strategy audit must not consult this file anymore.
    settings.paths.metadata.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"cal_date": [signal_date], "is_open": [1]}).to_parquet(
        settings.paths.metadata / "trade_calendar.parquet",
        index=False,
    )

    score_index = pd.MultiIndex.from_tuples(
        [(signal_date, instrument)],
        names=["datetime", "instrument"],
    )
    score = pd.Series([0.9], index=score_index, name="score")
    quote_index = pd.MultiIndex.from_tuples(
        [(instrument, trade_date)],
        names=["instrument", "datetime"],
    )
    raw = pd.DataFrame(
        {
            "$close": [10.0],
            "$is_limit_up": [np.nan],
            "$is_limit_down": [np.nan],
        },
        index=quote_index,
    )
    observed: dict[str, pd.Timestamp] = {}

    def features(*args, **kwargs):
        observed["start"] = pd.Timestamp(kwargs["start_time"])
        observed["end"] = pd.Timestamp(kwargs["end_time"])
        return raw.copy()

    monkeypatch.setattr("qlib.data.D.features", features)
    _patch_dataset_calendar(monkeypatch, signal_date, trade_date)

    view = _market_data_view(
        settings,
        score,
        StageTimings(),
        trade_dates=pd.DatetimeIndex([trade_date]),
    )

    assert observed == {"start": trade_date, "end": trade_date}
    assert view.quote["trade_date"].eq(trade_date).all()
    assert view.quote["instrument"].tolist() == [instrument]


def test_market_data_view_fails_closed_for_trade_date_outside_dataset_calendar(tmp_path, monkeypatch):
    signal_date = pd.Timestamp("2026-08-21")
    trade_date = pd.Timestamp("2026-08-24")
    score_index = pd.MultiIndex.from_tuples(
        [(signal_date, "SH600900")],
        names=["datetime", "instrument"],
    )
    score = pd.Series([0.9], index=score_index, name="score")

    _patch_dataset_calendar(monkeypatch, signal_date)
    monkeypatch.setattr(
        "qlib.data.D.features",
        lambda *args, **kwargs: pytest.fail("quote query must not run outside governed calendar"),
    )

    with pytest.raises(RuntimeError, match="outside the pinned DatasetVersion calendar"):
        _market_data_view(
            _settings(tmp_path),
            score,
            StageTimings(),
            trade_dates=pd.DatetimeIndex([trade_date]),
        )
