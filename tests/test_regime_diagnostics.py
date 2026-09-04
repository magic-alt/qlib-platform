from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qlib_platform.lineage import sha256_json
from qlib_platform.research.features.taxonomy import FactorTaxonomy, FactorTaxonomyEntry
from qlib_platform.research.diagnostics.regimes import RegimeSpec
from qlib_platform.research.diagnostics.regime_analysis import (
    ModelComparisonSpec,
    benjamini_hochberg,
    build_oriented_composites,
    derive_model_daily_metrics,
    derive_factor_regime_diagnostics,
    normalize_model_predictions,
    derive_topk_regime_overlap,
)


def _taxonomy() -> FactorTaxonomy:
    entries = {
        "VAL": FactorTaxonomyEntry("VAL", "Value", "alpha", "negative"),
        "VOL": FactorTaxonomyEntry("VOL", "Volatility", "alpha", "negative"),
        "MIN": FactorTaxonomyEntry("MIN", "Momentum", "alpha", "unknown"),
    }
    return FactorTaxonomy("test", "pack", entries, sha256_json({}), "file")


def _spec(minimum_sessions: int = 2) -> RegimeSpec:
    return RegimeSpec(
        regime_id="test",
        minimum_sessions=minimum_sessions,
        hac_lag=1,
        fdr_alpha=0.05,
        topk=2,
        stable_features=("VAL", "VOL"),
        hypothesis_features=("MIN",),
        composites={"value": ("VAL",), "low_vol": ("VOL",)},
        dimensions={},
        semantic_sha256="semantic",
        file_sha256="file",
    )


def test_benjamini_hochberg_preserves_index_and_monotone_adjustment():
    values = pd.Series([0.01, 0.04, 0.03, np.nan], index=list("abcd"))

    actual = benjamini_hochberg(values)

    assert actual.loc["a"] == pytest.approx(0.03)
    assert actual.loc["b"] == pytest.approx(0.04)
    assert actual.loc["c"] == pytest.approx(0.04)
    assert np.isnan(actual.loc["d"])


def test_composites_apply_only_predeclared_orientation():
    dates = pd.bdate_range("2025-01-02", periods=2)
    instruments = ["A", "B", "C"]
    index = pd.MultiIndex.from_product([dates, instruments], names=["datetime", "instrument"])
    features = pd.DataFrame(
        {
            "VAL": np.tile([1.0, 2.0, 3.0], 2),
            "VOL": np.tile([3.0, 2.0, 1.0], 2),
            "MIN": np.tile([1.0, 2.0, 3.0], 2),
        },
        index=index,
    )

    result = build_oriented_composites(features, _taxonomy(), _spec())

    assert result.loc[(dates[0], "A"), "value"] > result.loc[(dates[0], "C"), "value"]
    assert result.loc[(dates[0], "C"), "low_vol"] > result.loc[(dates[0], "A"), "low_vol"]
    assert "MIN" not in result


def test_factor_regime_diagnostics_never_orients_unknown_min_and_enforces_sample_gate():
    dates = pd.bdate_range("2025-01-02", periods=3)
    rows = []
    for feature, values in {
        "VAL": [-0.10, -0.20, -0.30],
        "VOL": [-0.05, -0.10, -0.15],
        "MIN": [0.05, 0.10, 0.15],
    }.items():
        for date, value in zip(dates, values, strict=True):
            rows.append({"date": date, "feature": feature, "rank_ic": value})
    labels = pd.DataFrame(
        {
            "date": dates,
            "dimension": "market_trend",
            "state": ["DOWN", "DOWN", "UP"],
            "status": "AVAILABLE",
        }
    )

    result = derive_factor_regime_diagnostics(
        pd.DataFrame(rows),
        labels,
        _taxonomy(),
        _spec(minimum_sessions=2),
        {date: f"fold_{number // 2}" for number, date in enumerate(dates)},
    )
    down = result.loc[result["state"].eq("DOWN")].set_index("feature")
    up = result.loc[result["state"].eq("UP")]

    assert down.loc["VAL", "oriented_rank_ic_mean"] == pytest.approx(0.15)
    assert down.loc["VAL", "positive_oriented_rank_ic_fold_ratio"] == pytest.approx(1.0)
    assert np.isnan(down.loc["MIN", "oriented_rank_ic_mean"])
    assert down.loc["MIN", "candidate_status"] == "HYPOTHESIS_ONLY"
    assert up["sample_status"].eq("INSUFFICIENT_SAMPLE").all()
    assert up["rank_ic_p_value"].isna().all()


def test_topk_overlap_is_grouped_by_causal_regime():
    dates = pd.bdate_range("2025-01-02", periods=2)
    instruments = ["A", "B", "C", "D"]
    index = pd.MultiIndex.from_product([dates, instruments], names=["datetime", "instrument"])
    scores = np.tile([4.0, 3.0, 2.0, 1.0], 2)
    predictions = {
        model: pd.DataFrame({"score": scores, "label": scores}, index=index)
        for model in ("ridge", "lightgbm", "xgboost")
    }
    composites = pd.DataFrame({"value": scores, "low_vol": np.tile([1.0, 2.0, 3.0, 4.0], 2)}, index=index)
    labels = pd.DataFrame(
        {
            "date": dates,
            "dimension": "market_trend",
            "state": "UP",
            "status": "AVAILABLE",
        }
    )

    result = derive_topk_regime_overlap(predictions, composites, labels, _spec())
    xgb = result.loc[result["model"].eq("xgboost")].set_index("composite")

    assert xgb.loc["value", "jaccard_mean"] == pytest.approx(1.0)
    assert xgb.loc["low_vol", "jaccard_mean"] == pytest.approx(0.0)


def test_model_diagnostics_accept_explicit_phase3_anchor_names_and_comparisons():
    dates = pd.bdate_range("2025-01-02", periods=2)
    instruments = ["A", "B", "C"]
    index = pd.MultiIndex.from_product([dates, instruments], names=["datetime", "instrument"])
    labels = pd.DataFrame({"label": np.tile([1.0, 2.0, 3.0], 2)}, index=index)
    predictions = {
        "P2-06_A4_RIDGE": pd.DataFrame({"score": np.tile([1.0, 2.0, 3.0], 2)}, index=index),
        "P2-07_A4_XGB": pd.DataFrame({"score": np.tile([3.0, 2.0, 1.0], 2)}, index=index),
    }
    normalized = normalize_model_predictions(predictions, labels, required_models=tuple(predictions))
    result = derive_model_daily_metrics(
        normalized,
        minimum_cross_section=3,
        model_comparisons=[ModelComparisonSpec("P2-07_A4_XGB", "P2-06_A4_RIDGE")],
    )

    comparison = result.loc[result["model"].eq("P2-07_A4_XGB_minus_P2-06_A4_RIDGE")]
    assert comparison["rank_ic"].eq(-2.0).all()
