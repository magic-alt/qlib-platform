from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qlib_platform.research.portfolio.overlays import (
    apply_gross_exposure,
    apply_lowvol_regime_weight,
    causal_volatility_scale,
)


def test_volatility_scale_is_t_minus_one_causal_and_clipped():
    index = pd.bdate_range("2025-01-01", periods=8)
    volatility = pd.Series([0.2, 0.2, 0.2, 0.2, 0.8, 0.1, 0.2, 0.2], index=index)
    scale = causal_volatility_scale(volatility, minimum_history=3)

    assert scale.iloc[:3].isna().all()
    assert scale.iloc[4] == pytest.approx(1.0)
    assert scale.iloc[5] == pytest.approx(0.5)
    assert scale.dropna().between(0.5, 1.0).all()


@pytest.mark.parametrize("weight", [1.0, 0.5, 0.0])
def test_lowvol_overlay_uses_only_predeclared_high_vol_weight(weight: float):
    index = pd.Index(["LOW", "HIGH"])
    components = pd.DataFrame(
        {"base_without_lowvol": [1.0, 1.0], "lowvol_contribution": [0.4, 0.4]}, index=index
    )
    result = apply_lowvol_regime_weight(
        components, pd.Series(["LOW", "HIGH"], index=index), high_vol_weight=weight
    )
    assert result.loc["LOW"] == pytest.approx(1.4)
    assert result.loc["HIGH"] == pytest.approx(1.0 + 0.4 * weight)


def test_gross_exposure_overlay_cannot_add_leverage():
    weights = pd.DataFrame({"A": [0.4, 0.4], "B": [0.4, 0.4]})
    result = apply_gross_exposure(weights, pd.Series([1.0, 0.5]))
    assert np.allclose(result.abs().sum(axis=1), [0.8, 0.4])
