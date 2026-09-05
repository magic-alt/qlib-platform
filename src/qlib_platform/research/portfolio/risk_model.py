from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from qlib_platform.research.portfolio.covariance import estimate_covariance, nearest_psd
from qlib_platform.research.portfolio.exposures import build_exposure_matrix


@dataclass(frozen=True)
class BarraLikeRiskModel:
    covariance: pd.DataFrame
    factor_exposures: pd.DataFrame
    factor_covariance: pd.DataFrame
    specific_variance: pd.Series
    factor_returns: pd.DataFrame
    annualization: float
    shrinkage: float

    @property
    def instruments(self) -> pd.Index:
        return self.covariance.index


def estimate_barra_like_risk(
    returns: pd.DataFrame,
    style_exposures: pd.DataFrame,
    industries: pd.Series | pd.DataFrame,
    *,
    annualization: float = 252.0,
    shrinkage: float = 0.10,
    ridge: float = 1e-6,
    min_observations: int = 20,
) -> BarraLikeRiskModel:
    if ridge < 0:
        raise ValueError("ridge must be non-negative")
    if returns.shape[0] < min_observations:
        raise ValueError("insufficient return history for risk-model estimation")
    exposures = build_exposure_matrix(style_exposures, industries)
    if not returns.columns.equals(exposures.index):
        raise ValueError("risk-model return columns must exactly match exposure instruments")
    numeric_returns = returns.apply(pd.to_numeric, errors="coerce")
    x = exposures.to_numpy(dtype=float)
    design = np.column_stack([np.ones(len(exposures), dtype=float), x])
    factor_rows: list[np.ndarray] = []
    residual_rows: list[np.ndarray] = []
    dates: list[object] = []
    for date, row in numeric_returns.iterrows():
        y = row.to_numpy(dtype=float)
        valid = np.isfinite(y)
        if int(valid.sum()) <= design.shape[1]:
            continue
        local_design = design[valid]
        penalty = np.eye(local_design.shape[1], dtype=float) * ridge
        penalty[0, 0] = 0.0
        beta = np.linalg.pinv(local_design.T @ local_design + penalty) @ local_design.T @ y[valid]
        residual = np.full(len(y), np.nan, dtype=float)
        residual[valid] = y[valid] - (design @ beta)[valid]
        factor_rows.append(beta[1:])
        residual_rows.append(residual)
        dates.append(date)
    if len(factor_rows) < min_observations:
        raise ValueError("insufficient complete cross-sections for factor-risk estimation")
    factor_returns = pd.DataFrame(factor_rows, index=dates, columns=exposures.columns)
    factor_covariance = estimate_covariance(
        factor_returns,
        shrinkage=shrinkage,
        annualization=annualization,
        min_observations=min_observations,
    )
    residuals = pd.DataFrame(residual_rows, index=dates, columns=returns.columns)
    specific = residuals.var(axis=0, ddof=1, skipna=True) * annualization
    if specific.isna().any():
        raise ValueError("specific variance cannot be estimated for every instrument")
    specific = specific.clip(lower=max(float(specific.median()) * 1e-4, 1e-10))
    asset_covariance = x @ factor_covariance.to_numpy(dtype=float) @ x.T + np.diag(
        specific.to_numpy(dtype=float)
    )
    return BarraLikeRiskModel(
        covariance=pd.DataFrame(
            nearest_psd(asset_covariance), index=returns.columns, columns=returns.columns
        ),
        factor_exposures=exposures,
        factor_covariance=factor_covariance,
        specific_variance=specific,
        factor_returns=factor_returns,
        annualization=float(annualization),
        shrinkage=float(shrinkage),
    )
