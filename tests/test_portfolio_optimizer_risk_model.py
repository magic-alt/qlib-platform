from __future__ import annotations

import numpy as np
import pandas as pd

from qlib_platform.research.portfolio import (
    OptimizationConfig,
    OptimizationConstraints,
    build_exposure_matrix,
    estimate_barra_like_risk,
    estimate_covariance,
    optimize_alpha_portfolio,
)


def test_covariance_is_psd_after_shrinkage() -> None:
    rng = np.random.default_rng(7)
    returns = pd.DataFrame(rng.normal(0, 0.01, size=(80, 5)), columns=list("ABCDE"))
    covariance = estimate_covariance(returns, shrinkage=0.2, min_observations=20)
    assert covariance.shape == (5, 5)
    assert np.linalg.eigvalsh(covariance.to_numpy()).min() >= -1e-10


def test_barra_like_risk_combines_style_industry_and_specific_risk() -> None:
    rng = np.random.default_rng(11)
    instruments = pd.Index(["A", "B", "C", "D", "E", "F"])
    style = pd.DataFrame(
        {
            "size": [-1.2, -0.7, -0.1, 0.2, 0.8, 1.3],
            "value": [0.9, 0.2, -0.4, 0.6, -0.7, -0.2],
        },
        index=instruments,
    )
    industries = pd.Series(["BANK", "BANK", "TECH", "TECH", "IND", "IND"], index=instruments)
    exposures = build_exposure_matrix(style, industries)
    factor_noise = rng.normal(0, 0.006, size=(70, exposures.shape[1]))
    idiosyncratic = rng.normal(0, 0.004, size=(70, len(instruments)))
    values = factor_noise @ exposures.to_numpy().T + idiosyncratic
    returns = pd.DataFrame(values, columns=instruments)
    risk = estimate_barra_like_risk(
        returns,
        style,
        industries,
        min_observations=20,
        shrinkage=0.1,
    )
    assert risk.covariance.index.equals(instruments)
    assert (risk.specific_variance > 0).all()
    assert np.linalg.eigvalsh(risk.covariance.to_numpy()).min() >= -1e-10


def test_optimizer_respects_weight_turnover_and_factor_bounds() -> None:
    instruments = pd.Index(["A", "B", "C", "D"])
    alpha = pd.Series([0.08, 0.05, 0.01, -0.01], index=instruments)
    covariance = pd.DataFrame(np.eye(4) * 0.04, index=instruments, columns=instruments)
    current = pd.Series([0.25, 0.25, 0.25, 0.25], index=instruments)
    exposures = pd.DataFrame(
        {"STYLE_SIZE": [-1.0, -0.3, 0.4, 1.0], "IND_TECH": [1.0, 1.0, 0.0, 0.0]},
        index=instruments,
    )
    constraints = OptimizationConstraints(
        max_weight=0.45,
        max_turnover=0.25,
        factor_bounds={"STYLE_SIZE": (-0.25, 0.25), "IND_TECH": (0.25, 0.65)},
    )
    result = optimize_alpha_portfolio(
        alpha,
        covariance,
        current_weights=current,
        exposures=exposures,
        constraints=constraints,
        config=OptimizationConfig(risk_aversion=1.0, step_size=0.08, max_iterations=800),
    )
    assert abs(float(result.weights.sum()) - 1.0) < 1e-7
    assert result.weights.max() <= 0.45 + 1e-7
    assert result.turnover <= 0.25 + 1e-6
    assert -0.25 - 1e-6 <= result.factor_exposures["STYLE_SIZE"] <= 0.25 + 1e-6
    assert 0.25 - 1e-6 <= result.factor_exposures["IND_TECH"] <= 0.65 + 1e-6


def test_optimizer_prefers_higher_alpha_when_risk_is_symmetric() -> None:
    instruments = pd.Index(["A", "B", "C"])
    alpha = pd.Series([0.10, 0.02, 0.00], index=instruments)
    covariance = pd.DataFrame(np.eye(3) * 0.01, index=instruments, columns=instruments)
    result = optimize_alpha_portfolio(
        alpha,
        covariance,
        constraints=OptimizationConstraints(max_weight=0.8),
        config=OptimizationConfig(risk_aversion=0.2, linear_turnover_cost=0, impact_cost=0),
    )
    assert result.weights["A"] > result.weights["B"] > result.weights["C"]
