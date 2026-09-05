from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qlib_platform.research.portfolio import (
    BarraLikeRiskModel,
    StressScenario,
    evaluate_stress_scenario,
    factor_risk_decomposition,
    portfolio_risk,
    tracking_risk,
)


def test_portfolio_risk_has_euler_mcr_and_incremental_contributions() -> None:
    instruments = pd.Index(["A", "B", "C"])
    covariance = pd.DataFrame(
        [
            [0.040, 0.010, 0.004],
            [0.010, 0.090, 0.006],
            [0.004, 0.006, 0.025],
        ],
        index=instruments,
        columns=instruments,
    )
    weights = pd.Series([0.50, 0.30, 0.20], index=instruments)

    result = portfolio_risk(weights, covariance)
    expected_variance = float(weights.to_numpy() @ covariance.to_numpy() @ weights.to_numpy())

    assert result.variance == pytest.approx(expected_variance)
    assert result.component_risk.sum() == pytest.approx(result.volatility)
    assert result.percent_contribution.sum() == pytest.approx(1.0)
    assert np.isfinite(result.marginal_risk).all()
    assert np.isfinite(result.incremental_risk).all()


def test_tracking_risk_is_risk_of_active_weights() -> None:
    instruments = pd.Index(["A", "B", "C"])
    covariance = pd.DataFrame(
        np.diag([0.04, 0.09, 0.16]),
        index=instruments,
        columns=instruments,
    )
    weights = pd.Series([0.50, 0.30, 0.20], index=instruments)
    benchmark = pd.Series([0.40, 0.40, 0.20], index=instruments)

    result = tracking_risk(weights, benchmark, covariance)
    active = weights - benchmark
    expected = float(np.sqrt(active.to_numpy() @ covariance.to_numpy() @ active.to_numpy()))

    assert result.active_weights.equals(active.rename("active_weight"))
    assert result.tracking_error == pytest.approx(expected)
    assert result.component_risk.sum() == pytest.approx(result.tracking_error)
    assert result.percent_contribution.sum() == pytest.approx(1.0)


def test_factor_risk_decomposition_reconciles_factor_and_specific_risk() -> None:
    instruments = pd.Index(["A", "B", "C"])
    factors = pd.Index(["STYLE_SIZE", "IND_TECH"])
    exposures = pd.DataFrame(
        [[-1.0, 1.0], [0.2, 1.0], [0.8, 0.0]],
        index=instruments,
        columns=factors,
    )
    factor_covariance = pd.DataFrame(
        [[0.040, 0.005], [0.005, 0.025]],
        index=factors,
        columns=factors,
    )
    specific = pd.Series([0.020, 0.015, 0.010], index=instruments)
    covariance = pd.DataFrame(
        exposures.to_numpy() @ factor_covariance.to_numpy() @ exposures.to_numpy().T
        + np.diag(specific.to_numpy()),
        index=instruments,
        columns=instruments,
    )
    risk_model = BarraLikeRiskModel(
        covariance=covariance,
        factor_exposures=exposures,
        factor_covariance=factor_covariance,
        specific_variance=specific,
        factor_returns=pd.DataFrame(columns=factors),
        annualization=252.0,
        shrinkage=0.1,
    )
    active_weights = pd.Series([0.10, -0.05, -0.05], index=instruments)

    result = factor_risk_decomposition(active_weights, risk_model)

    assert result.factor_variance + result.specific_variance == pytest.approx(result.total_variance)
    assert result.factor_share + result.specific_share == pytest.approx(1.0)
    assert result.factor_component_variance.sum() == pytest.approx(result.factor_variance)
    assert result.specific_component_variance.sum() == pytest.approx(result.specific_variance)
    assert result.factor_component_risk.sum() + result.specific_component_risk.sum() == pytest.approx(
        result.total_volatility
    )


def test_stress_scenario_combines_factor_asset_and_benchmark_relative_shocks() -> None:
    instruments = pd.Index(["A", "B", "C"])
    exposures = pd.DataFrame(
        {
            "MARKET": [1.1, 0.8, 1.0],
            "VALUE": [-0.2, 0.5, 0.1],
        },
        index=instruments,
    )
    weights = pd.Series([0.50, 0.30, 0.20], index=instruments)
    benchmark = pd.Series([0.40, 0.40, 0.20], index=instruments)
    scenario = StressScenario(
        name="market_drawdown",
        factor_shocks={"MARKET": -0.10, "VALUE": 0.02},
        asset_shocks={"C": -0.03},
    )

    result = evaluate_stress_scenario(
        weights,
        scenario,
        factor_exposures=exposures,
        benchmark_weights=benchmark,
    )
    expected_shocks = exposures.to_numpy() @ np.array([-0.10, 0.02]) + np.array([0.0, 0.0, -0.03])

    assert result.instrument_shocks.to_numpy() == pytest.approx(expected_shocks)
    assert result.portfolio_return == pytest.approx(float(weights.to_numpy() @ expected_shocks))
    assert result.benchmark_return == pytest.approx(float(benchmark.to_numpy() @ expected_shocks))
    assert result.active_return == pytest.approx(float((weights - benchmark).to_numpy() @ expected_shocks))
    assert result.factor_return + result.asset_specific_return == pytest.approx(result.portfolio_return)


def test_risk_platform_fails_closed_on_alignment_psd_and_unknown_scenario_factors() -> None:
    instruments = pd.Index(["A", "B"])
    non_psd = pd.DataFrame([[1.0, 2.0], [2.0, 1.0]], index=instruments, columns=instruments)
    weights = pd.Series([0.5, 0.5], index=instruments)

    with pytest.raises(ValueError, match="positive semidefinite"):
        portfolio_risk(weights, non_psd)

    valid_covariance = pd.DataFrame(np.eye(2), index=instruments, columns=instruments)
    misaligned = pd.Series([0.5, 0.5], index=pd.Index(["B", "A"]))
    with pytest.raises(ValueError, match="exactly match"):
        portfolio_risk(misaligned, valid_covariance)

    exposures = pd.DataFrame({"MARKET": [1.0, 1.0]}, index=instruments)
    with pytest.raises(ValueError, match="unknown factors"):
        evaluate_stress_scenario(
            weights,
            StressScenario(name="bad", factor_shocks={"UNKNOWN": -0.1}),
            factor_exposures=exposures,
        )
