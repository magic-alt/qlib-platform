from __future__ import annotations

import numpy as np
import pandas as pd

from qlib_platform.research.regime import (
    RegimeSpec,
    _expanding_quantile_states,
    build_regime_labels,
    load_regime_spec,
)


def _spec() -> RegimeSpec:
    dimensions = {
        "market_trend": {
            "window": 3,
            "classifier": "symmetric_threshold",
            "threshold": 0.01,
            "states": ["DOWN", "NEUTRAL", "UP"],
        },
        "market_volatility": {
            "window": 2,
            "annualization": 252,
            "classifier": "expanding_quantiles",
            "quantiles": [1 / 3, 2 / 3],
            "minHistory": 3,
            "states": ["LOW", "MID", "HIGH"],
        },
        "market_activity": {
            "field": "TURNOVER_F",
            "classifier": "expanding_quantiles",
            "quantiles": [1 / 3, 2 / 3],
            "minHistory": 3,
            "states": ["LOW", "NORMAL", "HIGH"],
        },
        "size_style": {
            "sizeField": "LOG_CIRC_MV",
            "basketQuantile": 0.30,
            "bucketLagSessions": 1,
            "window": 2,
            "classifier": "symmetric_threshold",
            "threshold": 0.01,
            "states": ["LARGE_CAP", "NEUTRAL", "SMALL_CAP"],
        },
        "industry_breadth": {
            "window": 2,
            "minimumIndustries": 3,
            "classifier": "expanding_quantiles",
            "quantiles": [1 / 3, 2 / 3],
            "minHistory": 3,
            "states": ["NARROW", "NORMAL", "BROAD"],
        },
    }
    return RegimeSpec(
        regime_id="test",
        minimum_sessions=2,
        hac_lag=1,
        fdr_alpha=0.05,
        topk=3,
        stable_features=("VAL", "VOL"),
        hypothesis_features=("MIN",),
        composites={"value": ("VAL",), "low_vol": ("VOL",)},
        dimensions=dimensions,
        semantic_sha256="semantic",
        file_sha256="file",
    )


def test_expanding_thresholds_use_history_as_of_t_minus_one():
    dates = pd.bdate_range("2025-01-02", periods=4)
    original = pd.Series([1.0, 2.0, 3.0, 100.0], index=dates)
    changed = pd.Series([1.0, 2.0, 3.0, -100.0], index=dates)

    left = _expanding_quantile_states(
        original, quantiles=(1 / 3, 2 / 3), min_history=3, states=("LOW", "MID", "HIGH")
    )
    right = _expanding_quantile_states(
        changed, quantiles=(1 / 3, 2 / 3), min_history=3, states=("LOW", "MID", "HIGH")
    )

    assert left.loc[dates[-1], "lower_threshold"] == right.loc[dates[-1], "lower_threshold"]
    assert left.loc[dates[-1], "upper_threshold"] == right.loc[dates[-1], "upper_threshold"]
    assert left.loc[dates[-1], "state"] == "HIGH"
    assert right.loc[dates[-1], "state"] == "LOW"


def test_expanding_thresholds_do_not_label_a_degenerate_history():
    dates = pd.bdate_range("2025-01-02", periods=4)

    result = _expanding_quantile_states(
        pd.Series(1.0, index=dates),
        quantiles=(1 / 3, 2 / 3),
        min_history=3,
        states=("LOW", "MID", "HIGH"),
    )

    assert result.loc[dates[-1], "state"] == "INSUFFICIENT_HISTORY"


def test_build_regime_labels_marks_missing_pit_industry_input_explicitly():
    dates = pd.bdate_range("2025-01-02", periods=10)
    instruments = [f"S{number:02d}" for number in range(20)]
    index = pd.MultiIndex.from_product([dates, instruments], names=["datetime", "instrument"])
    cross_section = np.tile(np.arange(20, dtype=float), len(dates))
    features = pd.DataFrame(
        {
            "TURNOVER_F": np.repeat(np.arange(1, 11, dtype=float), 20),
            "LOG_CIRC_MV": cross_section,
        },
        index=index,
    )
    returns = pd.Series(
        np.where(cross_section < 6, 0.02, np.where(cross_section >= 14, -0.01, 0.0)),
        index=index,
    )
    benchmark = pd.Series(np.linspace(100.0, 110.0, len(dates)), index=dates)

    result = build_regime_labels(
        _spec(),
        benchmark_close=benchmark,
        features=features,
        stock_returns=returns,
        industries=None,
        evaluation_dates=dates[-3:],
    )

    breadth = result.loc[result["dimension"].eq("industry_breadth")]
    assert breadth["state"].eq("INPUT_UNAVAILABLE").all()
    assert breadth["status"].eq("INPUT_UNAVAILABLE").all()
    assert result.loc[result["dimension"].eq("market_trend"), "status"].eq("AVAILABLE").all()


def test_repository_regime_config_is_predeclared_and_keeps_min_direction_unknown():
    spec = load_regime_spec("configs/regimes/ashare_regime_v1.yaml")

    assert spec.minimum_sessions == 63
    assert spec.topk == 30
    assert spec.hypothesis_features == ("MIN20", "MIN30", "MIN60")
    assert set(spec.composites) == {"value", "low_vol"}
