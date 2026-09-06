from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qlib_platform.research.portfolio import (
    OptimizationConfig,
    OptimizationConstraints,
    optimize_alpha_portfolio,
    optimized_target_portfolio,
    round_weights_to_lots,
)


def test_benchmark_relative_optimizer_respects_te_and_active_factor_bounds() -> None:
    instruments = pd.Index(["A", "B", "C", "D"])
    alpha = pd.Series([0.10, 0.06, 0.01, -0.02], index=instruments)
    covariance = pd.DataFrame(np.eye(4) * 0.04, index=instruments, columns=instruments)
    benchmark = pd.Series([0.25, 0.25, 0.25, 0.25], index=instruments)
    exposures = pd.DataFrame(
        {
            "STYLE_SIZE": [-1.0, -0.25, 0.25, 1.0],
            "IND_TECH": [1.0, 1.0, 0.0, 0.0],
        },
        index=instruments,
    )
    constraints = OptimizationConstraints(
        max_weight=0.60,
        max_tracking_error=0.035,
        active_factor_bounds={
            "STYLE_SIZE": (-0.08, 0.08),
            "IND_TECH": (-0.10, 0.10),
        },
    )

    result = optimize_alpha_portfolio(
        alpha,
        covariance,
        benchmark_weights=benchmark,
        exposures=exposures,
        constraints=constraints,
        config=OptimizationConfig(
            objective="benchmark_relative",
            risk_aversion=0.5,
            linear_turnover_cost=0.0,
            impact_cost=0.0,
            step_size=0.08,
            max_iterations=1_000,
        ),
    )

    assert result.objective_name == "benchmark_relative"
    assert result.tracking_error is not None
    assert result.tracking_error <= 0.035 + 1e-6
    assert result.active_factor_exposures is not None
    assert -0.08 - 1e-6 <= result.active_factor_exposures["STYLE_SIZE"] <= 0.08 + 1e-6
    assert -0.10 - 1e-6 <= result.active_factor_exposures["IND_TECH"] <= 0.10 + 1e-6
    assert result.weights["A"] > benchmark["A"]
    assert abs(float(result.weights.sum()) - 1.0) < 1e-7


def test_minimum_variance_profile_prefers_lower_variance_assets() -> None:
    instruments = pd.Index(["LOW", "MID", "HIGH"])
    alpha = pd.Series([0.0, 0.0, 0.0], index=instruments)
    covariance = pd.DataFrame(
        np.diag([0.01, 0.04, 0.09]),
        index=instruments,
        columns=instruments,
    )

    result = optimize_alpha_portfolio(
        alpha,
        covariance,
        constraints=OptimizationConstraints(max_weight=0.80),
        config=OptimizationConfig(
            objective="minimum_variance",
            linear_turnover_cost=0.0,
            impact_cost=0.0,
            step_size=0.10,
            max_iterations=1_500,
        ),
    )

    assert result.weights["LOW"] > result.weights["MID"] > result.weights["HIGH"]
    assert result.variance < float((np.ones(3) / 3.0) @ covariance.to_numpy() @ (np.ones(3) / 3.0))


def test_risk_parity_profile_matches_equal_risk_budgets() -> None:
    instruments = pd.Index(["A", "B", "C"])
    alpha = pd.Series([0.0, 0.0, 0.0], index=instruments)
    covariance = pd.DataFrame(
        np.diag([0.01, 0.04, 0.09]),
        index=instruments,
        columns=instruments,
    )

    result = optimize_alpha_portfolio(
        alpha,
        covariance,
        constraints=OptimizationConstraints(max_weight=0.70),
        config=OptimizationConfig(
            objective="risk_parity",
            linear_turnover_cost=0.0,
            impact_cost=0.0,
            max_iterations=1_000,
        ),
    )
    weights = result.weights.to_numpy(dtype=float)
    component_variance = weights * (covariance.to_numpy() @ weights)
    shares = component_variance / component_variance.sum()

    assert result.converged
    assert shares == pytest.approx(np.full(3, 1.0 / 3.0), abs=1e-6)
    assert result.weights["A"] > result.weights["B"] > result.weights["C"]


def test_robust_alpha_uncertainty_haircut_changes_preference() -> None:
    instruments = pd.Index(["A", "B", "C"])
    alpha = pd.Series([0.10, 0.08, 0.00], index=instruments)
    uncertainty = pd.Series([0.20, 0.00, 0.00], index=instruments)
    covariance = pd.DataFrame(np.eye(3) * 0.01, index=instruments, columns=instruments)
    constraints = OptimizationConstraints(max_weight=0.80)

    plain = optimize_alpha_portfolio(
        alpha,
        covariance,
        constraints=constraints,
        config=OptimizationConfig(
            risk_aversion=0.1,
            linear_turnover_cost=0.0,
            impact_cost=0.0,
        ),
    )
    robust = optimize_alpha_portfolio(
        alpha,
        covariance,
        constraints=constraints,
        alpha_uncertainty=uncertainty,
        config=OptimizationConfig(
            risk_aversion=0.1,
            linear_turnover_cost=0.0,
            impact_cost=0.0,
            robust_alpha_uncertainty_penalty=0.5,
            covariance_diagonal_buffer=0.25,
        ),
    )

    assert plain.weights["A"] > plain.weights["B"]
    assert robust.weights["B"] > robust.weights["A"]


def test_transaction_cost_budget_and_turnover_are_hard_constraints() -> None:
    instruments = pd.Index(["A", "B", "C", "D"])
    alpha = pd.Series([0.20, 0.10, -0.05, -0.10], index=instruments)
    covariance = pd.DataFrame(np.eye(4) * 0.02, index=instruments, columns=instruments)
    current = pd.Series([0.25, 0.25, 0.25, 0.25], index=instruments)
    linear = pd.Series([0.01, 0.01, 0.01, 0.01], index=instruments)
    impact = pd.Series([0.02, 0.02, 0.02, 0.02], index=instruments)
    constraints = OptimizationConstraints(
        max_weight=0.60,
        max_turnover=0.08,
        max_trade_cost=0.0017,
    )

    result = optimize_alpha_portfolio(
        alpha,
        covariance,
        current_weights=current,
        constraints=constraints,
        linear_costs=linear,
        impact_coefficients=impact,
        config=OptimizationConfig(
            risk_aversion=0.1,
            linear_turnover_cost=0.0,
            impact_cost=0.0,
            step_size=0.10,
        ),
    )

    assert result.turnover <= 0.08 + 1e-6
    assert result.linear_cost + result.impact_cost <= 0.0017 + 1e-8


def test_cardinality_and_minimum_position_constraints_create_sparse_portfolio() -> None:
    instruments = pd.Index(list("ABCDE"))
    alpha = pd.Series([0.10, 0.08, 0.04, 0.02, 0.00], index=instruments)
    covariance = pd.DataFrame(np.eye(5) * 0.02, index=instruments, columns=instruments)

    result = optimize_alpha_portfolio(
        alpha,
        covariance,
        constraints=OptimizationConstraints(
            max_weight=0.60,
            max_positions=2,
            min_position_weight=0.40,
        ),
        config=OptimizationConfig(
            risk_aversion=0.1,
            linear_turnover_cost=0.0,
            impact_cost=0.0,
        ),
    )

    active = result.weights[result.weights > 1e-12]
    assert result.position_count == 2
    assert len(active) == 2
    assert (active >= 0.40 - 1e-7).all()
    assert result.weights["A"] > 0
    assert result.weights["B"] > 0


def test_round_lot_implementation_preserves_valid_share_lots_and_cash() -> None:
    instruments = pd.Index(["A", "B", "C"])
    weights = pd.Series([0.50, 0.30, 0.20], index=instruments)
    prices = pd.Series([10.0, 20.0, 30.0], index=instruments)

    implementation = round_weights_to_lots(
        weights,
        prices,
        portfolio_value=100_000.0,
        lot_sizes=100,
    )

    assert (implementation.shares.to_numpy(dtype="int64") % 100 == 0).all()
    assert implementation.shares.to_dict() == {"A": 5000, "B": 1500, "C": 600}
    assert implementation.invested_value == pytest.approx(98_000.0)
    assert implementation.residual_cash == pytest.approx(2_000.0)
    assert implementation.weights.to_dict() == pytest.approx({"A": 0.50, "B": 0.30, "C": 0.18})


def test_optimized_target_portfolio_exposes_continuous_and_lot_sized_targets() -> None:
    instruments = pd.Index(["A", "B", "C"])
    alpha = pd.Series([0.10, 0.05, 0.01], index=instruments)
    covariance = pd.DataFrame(np.eye(3) * 0.02, index=instruments, columns=instruments)
    prices = pd.Series([10.0, 20.0, 30.0], index=instruments)

    frame, result = optimized_target_portfolio(
        alpha,
        covariance,
        constraints=OptimizationConstraints(max_weight=0.80),
        config=OptimizationConfig(linear_turnover_cost=0.0, impact_cost=0.0),
        prices=prices,
        portfolio_value=100_000.0,
    )

    assert "target_weight" in frame.columns
    assert "target_shares" in frame.columns
    assert "implemented_weight" in frame.columns
    assert (frame["target_shares"] % 100 == 0).all()
    assert frame.attrs["invested_value"] <= 100_000.0
    assert frame.attrs["residual_cash"] >= 0.0
    assert result.objective_name == "alpha_risk"


def test_benchmark_relative_features_fail_closed_without_aligned_benchmark() -> None:
    instruments = pd.Index(["A", "B", "C"])
    alpha = pd.Series([0.10, 0.05, 0.01], index=instruments)
    covariance = pd.DataFrame(np.eye(3) * 0.02, index=instruments, columns=instruments)

    with pytest.raises(ValueError, match="benchmark_weights are required"):
        optimize_alpha_portfolio(
            alpha,
            covariance,
            constraints=OptimizationConstraints(max_weight=0.80),
            config=OptimizationConfig(objective="benchmark_relative"),
        )

    misaligned = pd.Series([1 / 3, 1 / 3, 1 / 3], index=pd.Index(["B", "A", "C"]))
    with pytest.raises(ValueError, match="exactly match"):
        optimize_alpha_portfolio(
            alpha,
            covariance,
            benchmark_weights=misaligned,
            constraints=OptimizationConstraints(max_weight=0.80, max_tracking_error=0.05),
        )
