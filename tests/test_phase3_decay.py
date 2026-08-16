from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tushare_qlib.research.phase3_decay import attach_model_age, derive_model_age_decay


def _daily() -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=10)
    return pd.DataFrame(
        {
            "date": dates,
            "model": "anchor",
            "rank_ic": np.linspace(0.10, -0.08, len(dates)),
            "topk_spread": np.linspace(0.02, -0.01, len(dates)),
            "turnover": 0.1,
        }
    )


def _folds() -> list[dict[str, object]]:
    dates = pd.bdate_range("2025-01-02", periods=10)
    return [
        {
            "foldId": "rolling_01",
            "start": str(dates[0].date()),
            "end": str(dates[-1].date()),
            "trainEnd": "2024-12-31",
        }
    ]


def test_model_age_is_fold_local_and_decay_table_is_descriptive():
    aged = attach_model_age(_daily(), _folds())
    result = derive_model_age_decay(
        _daily(), _folds(), age_bucket_upper_sessions=[2, 5], hac_lag=1
    ).set_index("age_bucket")

    assert aged["model_age_sessions"].tolist() == list(range(10))
    assert list(result.index) == ["0-2", "3-5", "6+"]
    assert result.loc["0-2", "rank_ic_mean"] > result.loc["6+", "rank_ic_mean"]
    assert result["ageDefinition"].eq("sessions_since_fold_test_start").all()


def test_model_age_rejects_dates_outside_frozen_folds():
    daily = _daily()
    folds = _folds()
    folds[0]["end"] = str(daily.iloc[-2]["date"].date())

    with pytest.raises(ValueError, match="outside the frozen fold calendar"):
        attach_model_age(daily, folds)
