from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tushare_qlib.research.phase3_diagnostics import (
    derive_daily_stability_metrics,
    derive_failure_windows,
    derive_regime_transition_metrics,
    derive_rolling_stability_metrics,
)
from tushare_qlib.research.regime_diagnostics import ModelComparisonSpec


def _predictions() -> dict[str, pd.DataFrame]:
    dates = pd.bdate_range("2025-01-02", periods=8)
    instruments = [f"S{number}" for number in range(6)]
    index = pd.MultiIndex.from_product([dates, instruments], names=["datetime", "instrument"])
    labels = np.tile(np.arange(6, dtype=float), len(dates))
    candidate = np.concatenate(
        [np.arange(6, dtype=float) if day < 4 else np.arange(5, -1, -1, dtype=float) for day in range(8)]
    )
    baseline = np.tile(np.arange(6, dtype=float), len(dates))
    return {
        "candidate": pd.DataFrame({"score": candidate, "label": labels}, index=index),
        "baseline": pd.DataFrame({"score": baseline, "label": labels}, index=index),
    }


def _regimes() -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=8)
    return pd.DataFrame(
        {
            "date": dates,
            "dimension": "market_volatility",
            "state": ["LOW"] * 4 + ["HIGH"] * 4,
            "status": "AVAILABLE",
            "transition": [False, False, False, False, True, False, False, False],
        }
    )


def test_daily_and_rolling_failure_map_support_named_phase3_anchors():
    daily = derive_daily_stability_metrics(
        _predictions(),
        topk=2,
        minimum_cross_section=6,
        model_comparisons=[ModelComparisonSpec("candidate", "baseline")],
    )
    rolling = derive_rolling_stability_metrics(daily, [3])
    failures = derive_failure_windows(rolling, daily, _regimes())

    candidate = daily.loc[daily["model"].eq("candidate")]
    comparison = daily.loc[daily["model"].eq("candidate_minus_baseline")]
    assert candidate.iloc[0]["rank_ic"] == pytest.approx(1.0)
    assert candidate.iloc[-1]["rank_ic"] == pytest.approx(-1.0)
    assert candidate.iloc[0]["topk_spread"] == pytest.approx(4.0)
    assert comparison.iloc[-1]["rank_ic"] == pytest.approx(-2.0)
    assert daily["portfolio_metric_status"].eq("INPUT_UNAVAILABLE").all()
    assert not failures.empty
    assert set(failures["excess_source"]) == {"topk_forward_label_spread_proxy"}
    assert failures["regime_majority"].str.contains("market_volatility").all()


def test_transition_analysis_compares_pre_and_post_windows_without_claiming_confirmation():
    daily = derive_daily_stability_metrics(_predictions(), topk=2, minimum_cross_section=6)
    result = derive_regime_transition_metrics(daily, _regimes(), windows=[3])
    candidate = result.loc[result["model"].eq("candidate")].iloc[0]

    assert candidate["from_state"] == "LOW"
    assert candidate["to_state"] == "HIGH"
    assert candidate["event_count"] == 1
    assert candidate["delta_rank_ic"] < 0
    assert candidate["after_failure_probability"] > candidate["before_failure_probability"]
