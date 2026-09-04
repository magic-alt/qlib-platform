from __future__ import annotations

import numpy as np
import pandas as pd

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
    score_index = pd.MultiIndex.from_product(
        [[signal_date], instruments], names=["datetime", "instrument"]
    )
    score = pd.Series([0.9, 0.1], index=score_index, name="score")
    quote_index = pd.MultiIndex.from_product(
        [instruments, [trade_date]], names=["instrument", "datetime"]
    )
    raw = pd.DataFrame(
        {
            "$close": [10.0, 11.0],
            "$is_limit_up": [np.nan, np.nan],
            "$is_limit_down": [np.nan, np.nan],
        },
        index=quote_index,
    )

    monkeypatch.setattr("qlib.data.D.features", lambda *args, **kwargs: raw.copy())
    monkeypatch.setattr(
        "qlib_platform.research.train_select._official_calendar",
        lambda settings: pd.DatetimeIndex([signal_date, trade_date]),
    )

    view = _market_data_view(_settings(tmp_path), score, StageTimings())
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
    score_index = pd.MultiIndex.from_product(
        [[signal_date], instruments], names=["datetime", "instrument"]
    )
    score = pd.Series([0.9, 0.1], index=score_index, name="score")
    quote_index = pd.MultiIndex.from_product(
        [instruments, [trade_date]], names=["instrument", "datetime"]
    )
    raw = pd.DataFrame(
        {
            "$close": [10.0, 11.0],
            "$is_limit_up": [1.0, -1.0],
            "$is_limit_down": [0.0, 2.0],
        },
        index=quote_index,
    )

    monkeypatch.setattr("qlib.data.D.features", lambda *args, **kwargs: raw.copy())
    monkeypatch.setattr(
        "qlib_platform.research.train_select._official_calendar",
        lambda settings: pd.DatetimeIndex([signal_date, trade_date]),
    )

    view = _market_data_view(_settings(tmp_path), score, StageTimings())
    quote = view.quote.set_index("instrument")

    assert quote.at["SH600900", "is_limit_up"] == 1.0
    assert quote.at["SH600930", "is_limit_up"] == -1.0
    assert quote.at["SH600900", "is_limit_down"] == 0.0
    assert quote.at["SH600930", "is_limit_down"] == 2.0

    normalized = _normalise_quotes(view.quote.drop(columns=["trade_date"]), required=True)
    assert normalized.at["SH600900", "is_limit_up"] == 1.0
    assert normalized.at["SH600930", "is_limit_up"] == 0.0
    assert normalized.at["SH600930", "is_limit_down"] == 1.0
