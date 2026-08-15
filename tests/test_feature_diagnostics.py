from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tushare_qlib.lineage import sha256_json
from tushare_qlib.research.factor_taxonomy import FactorTaxonomy, FactorTaxonomyEntry
from tushare_qlib.research.feature_diagnostics import (
    FeatureDiagnosticsSpec,
    align_oos_features,
    build_feature_diagnostics,
    derive_factor_quantile_returns,
    derive_feature_daily_diagnostics,
    newey_west_t,
)


def _taxonomy() -> FactorTaxonomy:
    entries = {
        "POS": FactorTaxonomyEntry("POS", "Momentum", "alpha", "positive"),
        "NEG": FactorTaxonomyEntry("NEG", "Value", "alpha", "negative"),
        "CONST": FactorTaxonomyEntry("CONST", "TechnicalOther", "alpha", "unknown"),
        "MISS": FactorTaxonomyEntry("MISS", "StateSupport", "support", "unknown"),
    }
    return FactorTaxonomy("test", "pack", entries, sha256_json({}), "file")


def _panel(days: int = 4, instruments: int = 10) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = pd.bdate_range("2025-01-02", periods=days)
    names = [f"S{number:02d}" for number in range(instruments)]
    index = pd.MultiIndex.from_product([dates, names], names=["datetime", "instrument"])
    base = np.tile(np.arange(instruments, dtype=float), days)
    labels = pd.DataFrame({"label": base}, index=index)
    features = pd.DataFrame(
        {
            "POS": base,
            "NEG": -base,
            "CONST": np.ones(len(index)),
            "MISS": base,
        },
        index=index,
    )
    features.loc[(dates[0], names[:4]), "MISS"] = np.nan
    return features, labels


def test_daily_ic_handles_perfect_inverse_constant_threshold_and_missingness():
    features, labels = _panel(days=1)
    spec = FeatureDiagnosticsSpec(min_cross_section=7, rolling_sessions=3, short_rolling_sessions=2)

    daily = derive_feature_daily_diagnostics(features, labels, _taxonomy(), spec).set_index("feature")

    assert daily.loc["POS", "rank_ic"] == pytest.approx(1.0)
    assert daily.loc["NEG", "rank_ic"] == pytest.approx(-1.0)
    assert np.isnan(daily.loc["CONST", "ic"])
    assert np.isnan(daily.loc["MISS", "rank_ic"])
    assert daily.loc["MISS", "coverage"] == pytest.approx(0.6)
    assert daily.loc["MISS", "missing_rate"] == pytest.approx(0.4)


def test_rolling_fold_year_and_orientation_are_oos_only():
    features, labels = _panel(days=4)
    dates = pd.DatetimeIndex(labels.index.get_level_values("datetime").unique())
    assignments = {date: "fold_0" if number < 2 else "fold_1" for number, date in enumerate(dates)}
    spec = FeatureDiagnosticsSpec(
        min_cross_section=5,
        rolling_sessions=3,
        short_rolling_sessions=2,
        quantiles=5,
    )

    result = build_feature_diagnostics(
        features,
        labels,
        _taxonomy(),
        spec,
        fold_assignments=assignments,
        hac_lag=2,
    )

    pos_rolling = result.rolling.loc[result.rolling["feature"].eq("POS")].reset_index(drop=True)
    assert np.isnan(pos_rolling.loc[1, "rolling_rank_ic_mean"])
    assert pos_rolling.loc[2, "rolling_rank_ic_mean"] == pytest.approx(1.0)
    neg_summary = result.summary.set_index("feature").loc["NEG"]
    assert neg_summary["rank_ic_mean"] == pytest.approx(-1.0)
    assert neg_summary["oriented_rank_ic_mean"] == pytest.approx(1.0)
    assert neg_summary["positive_oriented_rank_ic_fold_ratio"] == pytest.approx(1.0)
    assert len(result.fold.loc[result.fold["feature"].eq("POS")]) == 2


def test_quantile_returns_preserve_raw_and_oriented_spreads_and_turnover():
    features, labels = _panel(days=2)
    second = labels.index.get_level_values("datetime").unique()[1]
    reversed_values = np.arange(9, -1, -1, dtype=float)
    features.loc[second, "POS"] = reversed_values
    labels.loc[second, "label"] = reversed_values
    spec = FeatureDiagnosticsSpec(min_cross_section=5, rolling_sessions=3, short_rolling_sessions=2)

    quantiles = derive_factor_quantile_returns(features, labels, _taxonomy(), spec)
    pos = quantiles.loc[quantiles["feature"].eq("POS")].reset_index(drop=True)
    neg = quantiles.loc[quantiles["feature"].eq("NEG")].reset_index(drop=True)
    const = quantiles.loc[quantiles["feature"].eq("CONST")]

    assert pos.loc[0, "raw_q5_minus_q1"] > 0
    assert pos.loc[1, "top_quantile_turnover"] == pytest.approx(1.0)
    assert neg.loc[0, "raw_q5_minus_q1"] < 0
    assert neg.loc[0, "oriented_long_short"] > 0
    assert const["raw_q5_minus_q1"].isna().all()


def test_newey_west_is_finite_and_rejects_degenerate_series():
    assert np.isfinite(newey_west_t(pd.Series([0.1, 0.2, -0.1, 0.3, 0.2]), lag=2))
    assert np.isnan(newey_west_t(pd.Series([1.0]), lag=1))


def test_strict_oos_reindex_rejects_a_missing_snapshot_key():
    features, labels = _panel(days=1)

    with pytest.raises(ValueError, match="missing 1 rolling OOS keys"):
        align_oos_features(features.iloc[1:], labels)
