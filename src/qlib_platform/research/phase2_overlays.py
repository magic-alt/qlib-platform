from __future__ import annotations

import numpy as np
import pandas as pd


def causal_volatility_scale(
    realized_volatility: pd.Series,
    *,
    minimum_history: int = 252,
    lower: float = 0.5,
    upper: float = 1.0,
) -> pd.Series:
    if minimum_history <= 0 or not 0 < lower <= upper <= 1:
        raise ValueError("invalid causal volatility scaling contract")
    volatility = pd.to_numeric(realized_volatility, errors="coerce").sort_index()
    lagged = volatility.shift(1)
    reference = lagged.expanding(min_periods=minimum_history).median()
    scale = reference.div(lagged.where(lagged.gt(0))).clip(lower=lower, upper=upper)
    scale.name = "gross_exposure_scale"
    return scale


def apply_lowvol_regime_weight(
    components: pd.DataFrame,
    regime_states: pd.Series,
    *,
    high_vol_weight: float,
) -> pd.Series:
    if not 0 <= high_vol_weight <= 1:
        raise ValueError("high-vol LowVol weight must be in [0, 1]")
    required = {"base_without_lowvol", "lowvol_contribution"}
    if missing := required - set(components):
        raise ValueError(f"score components are missing: {sorted(missing)}")
    if not components.index.equals(regime_states.index):
        raise ValueError("score components and causal regime states must align exactly")
    weights = pd.Series(1.0, index=components.index)
    weights.loc[regime_states.eq("HIGH")] = high_vol_weight
    result = pd.to_numeric(components["base_without_lowvol"], errors="coerce") + weights * pd.to_numeric(
        components["lowvol_contribution"], errors="coerce"
    )
    result.name = "adjusted_score"
    return result


def apply_gross_exposure(weights: pd.DataFrame, scale: pd.Series) -> pd.DataFrame:
    if not weights.index.equals(scale.index):
        raise ValueError("portfolio weights and exposure scale must align exactly")
    numeric = weights.apply(pd.to_numeric, errors="coerce")
    result = numeric.mul(scale, axis=0)
    gross = result.abs().sum(axis=1)
    if np.isfinite(gross).any() and gross.max() > 1.0 + 1e-12:
        raise ValueError("volatility overlay must never introduce leverage")
    return result
