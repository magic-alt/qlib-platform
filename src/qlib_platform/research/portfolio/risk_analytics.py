from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from qlib_platform.research.portfolio.risk_model import BarraLikeRiskModel


_PSD_TOLERANCE = 1e-9


@dataclass(frozen=True)
class RiskBreakdown:
    """Euler-compatible portfolio risk decomposition."""

    variance: float
    volatility: float
    marginal_risk: pd.Series
    component_risk: pd.Series
    percent_contribution: pd.Series
    incremental_risk: pd.Series


@dataclass(frozen=True)
class TrackingRiskBreakdown:
    """Benchmark-relative risk decomposition using active weights."""

    active_weights: pd.Series
    variance: float
    tracking_error: float
    marginal_risk: pd.Series
    component_risk: pd.Series
    percent_contribution: pd.Series
    incremental_risk: pd.Series


@dataclass(frozen=True)
class FactorRiskBreakdown:
    """Factor/specific risk decomposition for a Barra-like risk model."""

    weights: pd.Series
    factor_exposure: pd.Series
    total_variance: float
    total_volatility: float
    factor_variance: float
    specific_variance: float
    factor_share: float
    specific_share: float
    factor_component_variance: pd.Series
    specific_component_variance: pd.Series
    factor_component_risk: pd.Series
    specific_component_risk: pd.Series


def _validate_covariance(covariance: pd.DataFrame) -> tuple[pd.Index, np.ndarray]:
    if covariance.empty or covariance.shape[0] != covariance.shape[1]:
        raise ValueError("covariance must be a non-empty square matrix")
    if covariance.index.has_duplicates or covariance.columns.has_duplicates:
        raise ValueError("covariance instruments must be unique")
    if not covariance.index.equals(covariance.columns):
        raise ValueError("covariance rows and columns must have identical ordered instruments")
    values = covariance.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("covariance must contain only finite values")
    if not np.allclose(values, values.T, rtol=1e-9, atol=1e-12):
        raise ValueError("covariance must be symmetric")
    minimum_eigenvalue = float(np.linalg.eigvalsh((values + values.T) / 2.0).min())
    if minimum_eigenvalue < -_PSD_TOLERANCE:
        raise ValueError(f"covariance must be positive semidefinite; min eigenvalue={minimum_eigenvalue:.3e}")
    return covariance.index, values


def _validate_weights(
    weights: pd.Series, instruments: pd.Index, *, name: str
) -> tuple[pd.Series, np.ndarray]:
    if weights.index.has_duplicates:
        raise ValueError(f"{name} instruments must be unique")
    if not weights.index.equals(instruments):
        raise ValueError(f"{name} instruments must exactly match covariance instruments and order")
    numeric = pd.to_numeric(weights, errors="coerce")
    values = numeric.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError(f"{name} must contain only finite values")
    return pd.Series(values, index=instruments, name=weights.name, dtype=float), values


def portfolio_risk(weights: pd.Series, covariance: pd.DataFrame) -> RiskBreakdown:
    """Calculate volatility, MCR, component risk and leave-one-position-out ICR.

    ``marginal_risk`` is the derivative of portfolio volatility with respect to
    each position weight. ``component_risk`` is the Euler contribution
    ``weight * marginal_risk``. ``incremental_risk`` is the change in portfolio
    volatility relative to setting that one position weight to zero while
    leaving all other positions unchanged.
    """

    instruments, covariance_values = _validate_covariance(covariance)
    _, weight_values = _validate_weights(weights, instruments, name="weights")
    covariance_times_weights = covariance_values @ weight_values
    variance = float(weight_values @ covariance_times_weights)
    if variance < -_PSD_TOLERANCE:
        raise ValueError("portfolio variance is negative beyond numerical tolerance")
    variance = max(0.0, variance)
    volatility = float(np.sqrt(variance))

    if volatility <= _PSD_TOLERANCE:
        marginal = np.zeros(len(instruments), dtype=float)
        component = np.zeros(len(instruments), dtype=float)
        percent = np.zeros(len(instruments), dtype=float)
    else:
        marginal = covariance_times_weights / volatility
        component = weight_values * marginal
        percent = component / volatility

    diagonal = np.diag(covariance_values)
    variance_without = (
        variance - 2.0 * weight_values * covariance_times_weights + (weight_values**2) * diagonal
    )
    variance_without = np.maximum(variance_without, 0.0)
    incremental = volatility - np.sqrt(variance_without)

    return RiskBreakdown(
        variance=variance,
        volatility=volatility,
        marginal_risk=pd.Series(marginal, index=instruments, name="marginal_risk"),
        component_risk=pd.Series(component, index=instruments, name="component_risk"),
        percent_contribution=pd.Series(percent, index=instruments, name="percent_contribution"),
        incremental_risk=pd.Series(incremental, index=instruments, name="incremental_risk"),
    )


def tracking_risk(
    weights: pd.Series,
    benchmark_weights: pd.Series,
    covariance: pd.DataFrame,
) -> TrackingRiskBreakdown:
    """Calculate benchmark-relative active risk and its contributions."""

    instruments, _ = _validate_covariance(covariance)
    portfolio, portfolio_values = _validate_weights(weights, instruments, name="weights")
    benchmark, benchmark_values = _validate_weights(
        benchmark_weights,
        instruments,
        name="benchmark_weights",
    )
    active_values = portfolio_values - benchmark_values
    active = pd.Series(active_values, index=instruments, name="active_weight")
    breakdown = portfolio_risk(active, covariance)
    return TrackingRiskBreakdown(
        active_weights=active,
        variance=breakdown.variance,
        tracking_error=breakdown.volatility,
        marginal_risk=breakdown.marginal_risk.rename("marginal_tracking_risk"),
        component_risk=breakdown.component_risk.rename("component_tracking_risk"),
        percent_contribution=breakdown.percent_contribution.rename("tracking_risk_percent_contribution"),
        incremental_risk=breakdown.incremental_risk.rename("incremental_tracking_risk"),
    )


def factor_risk_decomposition(
    weights: pd.Series,
    risk_model: BarraLikeRiskModel,
) -> FactorRiskBreakdown:
    """Decompose modeled portfolio variance into factor and specific components.

    Passing active weights (portfolio minus benchmark) produces the corresponding
    benchmark-relative factor-risk decomposition without a separate code path.
    """

    instruments = risk_model.instruments
    normalized_weights, weight_values = _validate_weights(weights, instruments, name="weights")
    exposures = risk_model.factor_exposures
    if not exposures.index.equals(instruments):
        raise ValueError("factor exposures must exactly match risk-model instruments")
    if exposures.columns.has_duplicates:
        raise ValueError("factor names must be unique")
    factor_covariance = risk_model.factor_covariance
    if not factor_covariance.index.equals(exposures.columns) or not factor_covariance.columns.equals(
        exposures.columns
    ):
        raise ValueError("factor covariance must exactly match factor exposure columns")
    if not risk_model.specific_variance.index.equals(instruments):
        raise ValueError("specific variance must exactly match risk-model instruments")

    exposure_values = exposures.to_numpy(dtype=float)
    factor_covariance_values = factor_covariance.to_numpy(dtype=float)
    specific_values = risk_model.specific_variance.to_numpy(dtype=float)
    if (
        not np.isfinite(exposure_values).all()
        or not np.isfinite(factor_covariance_values).all()
        or not np.isfinite(specific_values).all()
    ):
        raise ValueError("risk-model decomposition inputs must be finite")
    if np.any(specific_values < 0):
        raise ValueError("specific variances must be non-negative")

    factor_exposure_values = exposure_values.T @ weight_values
    factor_covariance_times_exposure = factor_covariance_values @ factor_exposure_values
    factor_component_variance_values = factor_exposure_values * factor_covariance_times_exposure
    factor_variance = float(factor_component_variance_values.sum())
    specific_component_variance_values = (weight_values**2) * specific_values
    specific_variance = float(specific_component_variance_values.sum())
    total_variance = factor_variance + specific_variance
    if total_variance < -_PSD_TOLERANCE:
        raise ValueError("modeled total variance is negative beyond numerical tolerance")
    total_variance = max(0.0, total_variance)
    total_volatility = float(np.sqrt(total_variance))

    if total_variance <= _PSD_TOLERANCE:
        factor_share = 0.0
        specific_share = 0.0
    else:
        factor_share = factor_variance / total_variance
        specific_share = specific_variance / total_variance

    if total_volatility <= _PSD_TOLERANCE:
        factor_component_risk_values = np.zeros_like(factor_component_variance_values)
        specific_component_risk_values = np.zeros_like(specific_component_variance_values)
    else:
        factor_component_risk_values = factor_component_variance_values / total_volatility
        specific_component_risk_values = specific_component_variance_values / total_volatility

    return FactorRiskBreakdown(
        weights=normalized_weights,
        factor_exposure=pd.Series(
            factor_exposure_values,
            index=exposures.columns,
            name="factor_exposure",
        ),
        total_variance=total_variance,
        total_volatility=total_volatility,
        factor_variance=factor_variance,
        specific_variance=specific_variance,
        factor_share=float(factor_share),
        specific_share=float(specific_share),
        factor_component_variance=pd.Series(
            factor_component_variance_values,
            index=exposures.columns,
            name="factor_component_variance",
        ),
        specific_component_variance=pd.Series(
            specific_component_variance_values,
            index=instruments,
            name="specific_component_variance",
        ),
        factor_component_risk=pd.Series(
            factor_component_risk_values,
            index=exposures.columns,
            name="factor_component_risk",
        ),
        specific_component_risk=pd.Series(
            specific_component_risk_values,
            index=instruments,
            name="specific_component_risk",
        ),
    )
