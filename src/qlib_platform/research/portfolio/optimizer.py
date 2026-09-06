from __future__ import annotations

import numpy as np
import pandas as pd

from qlib_platform.research.portfolio.optimizer_constraints import (
    OptimizationConstraints,
    apply_position_constraints,
    project_constraints,
    tracking_error,
    turnover,
)
from qlib_platform.research.portfolio.optimizer_inputs import (
    aligned_optional,
    benchmark_vector,
    current_vector,
    exposure_matrix,
    risk_budget_vector,
    validate_inputs,
)
from qlib_platform.research.portfolio.optimizer_types import OptimizationConfig, OptimizationResult


def _risk_parity_seed(
    covariance: np.ndarray,
    budgets: np.ndarray,
    *,
    target_exposure: float,
    max_iterations: int,
    tolerance: float,
) -> np.ndarray:
    diagonal = np.diag(covariance)
    if bool(np.any(diagonal <= 1e-14)):
        raise ValueError("risk-parity covariance must have strictly positive diagonal variance")
    weights = np.ones(len(budgets), dtype=float)
    for _ in range(max_iterations):
        before = weights.copy()
        for position in range(len(weights)):
            cross = float(covariance[position] @ weights - diagonal[position] * weights[position])
            discriminant = cross**2 + 4.0 * diagonal[position] * budgets[position]
            weights[position] = (-cross + float(np.sqrt(max(0.0, discriminant)))) / (2.0 * diagonal[position])
        if float(np.max(np.abs(weights - before))) <= tolerance:
            break
    total = float(weights.sum())
    if not np.isfinite(weights).all() or total <= 0:
        raise ValueError("risk-parity solver did not produce valid positive weights")
    result: np.ndarray = np.asarray(weights / total * target_exposure, dtype=float)
    return result


def _risk_parity_shares(weights: np.ndarray, covariance: np.ndarray) -> np.ndarray:
    component_variance = weights * (covariance @ weights)
    total_variance = float(component_variance.sum())
    if total_variance <= 1e-18 or bool(np.any(component_variance <= 0)):
        raise ValueError("risk parity requires strictly positive component risk contributions")
    return component_variance / total_variance


def optimize_alpha_portfolio(
    alpha: pd.Series,
    covariance: pd.DataFrame,
    *,
    current_weights: pd.Series | None = None,
    benchmark_weights: pd.Series | None = None,
    exposures: pd.DataFrame | None = None,
    constraints: OptimizationConstraints | None = None,
    config: OptimizationConfig | None = None,
    linear_costs: pd.Series | None = None,
    impact_coefficients: pd.Series | None = None,
    alpha_uncertainty: pd.Series | None = None,
    risk_budgets: pd.Series | None = None,
) -> OptimizationResult:
    instruments, alpha_values, covariance_values = validate_inputs(alpha, covariance)
    resolved_constraints = constraints or OptimizationConstraints()
    resolved_config = config or OptimizationConfig()
    resolved_constraints.validate(len(instruments))
    resolved_config.validate()

    benchmark_required = (
        resolved_config.objective == "benchmark_relative"
        or resolved_constraints.max_tracking_error is not None
        or bool(resolved_constraints.active_factor_bounds)
    )
    benchmark = benchmark_vector(
        benchmark_weights,
        instruments,
        resolved_constraints,
        required=benchmark_required,
    )
    current = current_vector(current_weights, instruments, resolved_constraints)
    exposure_values, exposure_columns = exposure_matrix(
        exposures,
        instruments,
        resolved_constraints,
    )
    linear = aligned_optional(
        linear_costs,
        instruments,
        resolved_config.linear_turnover_cost,
        "linear_costs",
    )
    impact = aligned_optional(
        impact_coefficients,
        instruments,
        resolved_config.impact_cost,
        "impact_coefficients",
    )
    uncertainty = aligned_optional(
        alpha_uncertainty,
        instruments,
        0.0,
        "alpha_uncertainty",
    )
    effective_alpha = alpha_values - resolved_config.robust_alpha_uncertainty_penalty * uncertainty
    optimization_covariance = covariance_values + resolved_config.covariance_diagonal_buffer * np.diag(
        np.maximum(np.diag(covariance_values), 1e-12)
    )

    risk_budgets_values: np.ndarray | None = None
    if resolved_config.objective == "risk_parity":
        risk_budgets_values = risk_budget_vector(risk_budgets, instruments)
    elif risk_budgets is not None:
        raise ValueError("risk_budgets are only valid for the risk_parity objective")

    if resolved_config.objective == "risk_parity":
        assert risk_budgets_values is not None
        initial = _risk_parity_seed(
            optimization_covariance,
            risk_budgets_values,
            target_exposure=resolved_constraints.target_exposure,
            max_iterations=min(2_000, resolved_config.max_iterations),
            tolerance=resolved_config.tolerance,
        )
    elif resolved_config.objective == "benchmark_relative" and benchmark is not None:
        initial = benchmark.copy()
    else:
        initial = current.copy()

    weights = project_constraints(
        initial,
        current,
        resolved_constraints,
        exposure_values,
        exposure_columns,
        benchmark=benchmark,
        covariance=optimization_covariance,
        linear_costs=linear,
        impact_coefficients=impact,
    )
    converged = False
    iterations = 0

    for iteration in range(resolved_config.max_iterations):
        if resolved_config.objective == "risk_parity":
            assert risk_budgets_values is not None
            shares = _risk_parity_shares(weights, optimization_covariance)
            ratio = np.sqrt(risk_budgets_values / np.maximum(shares, 1e-15))
            raw_candidate = weights * ratio
        else:
            delta = weights - current
            if resolved_config.objective == "benchmark_relative":
                assert benchmark is not None
                risk_vector = weights - benchmark
                alpha_gradient = -effective_alpha
                risk_gradient = 2.0 * resolved_config.risk_aversion * (optimization_covariance @ risk_vector)
            elif resolved_config.objective == "minimum_variance":
                alpha_gradient = np.zeros_like(effective_alpha)
                risk_gradient = 2.0 * (optimization_covariance @ weights)
            else:
                alpha_gradient = -effective_alpha
                risk_gradient = 2.0 * resolved_config.risk_aversion * (optimization_covariance @ weights)
            gradient = alpha_gradient + risk_gradient + linear * np.sign(delta) + 2.0 * impact * delta
            raw_candidate = weights - resolved_config.step_size / np.sqrt(iteration + 1.0) * gradient

        candidate = project_constraints(
            raw_candidate,
            current,
            resolved_constraints,
            exposure_values,
            exposure_columns,
            benchmark=benchmark,
            covariance=optimization_covariance,
            linear_costs=linear,
            impact_coefficients=impact,
        )
        iterations = iteration + 1
        weight_change = float(np.max(np.abs(candidate - weights)))
        weights = candidate
        if resolved_config.objective == "risk_parity":
            assert risk_budgets_values is not None
            share_error = float(
                np.max(np.abs(_risk_parity_shares(weights, optimization_covariance) - risk_budgets_values))
            )
            if weight_change <= resolved_config.tolerance and share_error <= max(
                resolved_config.tolerance,
                1e-6,
            ):
                converged = True
                break
        elif weight_change <= resolved_config.tolerance:
            converged = True
            break

    weights = apply_position_constraints(
        weights,
        current,
        resolved_constraints,
        exposure_values,
        exposure_columns,
        benchmark=benchmark,
        covariance=optimization_covariance,
        linear_costs=linear,
        impact_coefficients=impact,
    )

    delta = weights - current
    expected_return = float(alpha_values @ weights)
    variance = float(weights @ covariance_values @ weights)
    linear_cost = float(linear @ np.abs(delta))
    impact_cost = float(impact @ (delta**2))
    factor_exposure = (
        {
            factor: float(weights @ exposure_values[:, position])
            for position, factor in enumerate(exposure_columns)
        }
        if exposure_values is not None
        else {}
    )
    benchmark_expected_return = float(alpha_values @ benchmark) if benchmark is not None else None
    active_factor_exposure = (
        {
            factor: factor_exposure[factor] - float(benchmark @ exposure_values[:, position])
            for position, factor in enumerate(exposure_columns)
        }
        if benchmark is not None and exposure_values is not None
        else {}
    )
    reported_tracking_error = (
        tracking_error(weights, benchmark, covariance_values) if benchmark is not None else None
    )

    if resolved_config.objective == "benchmark_relative":
        assert benchmark is not None
        active = weights - benchmark
        robust_active_variance = float(active @ optimization_covariance @ active)
        objective = (
            float(effective_alpha @ active)
            - resolved_config.risk_aversion * robust_active_variance
            - linear_cost
            - impact_cost
        )
    elif resolved_config.objective == "minimum_variance":
        robust_variance = float(weights @ optimization_covariance @ weights)
        objective = -robust_variance - linear_cost - impact_cost
    elif resolved_config.objective == "risk_parity":
        assert risk_budgets_values is not None
        share_error = _risk_parity_shares(weights, optimization_covariance) - risk_budgets_values
        objective = -float(share_error @ share_error) - linear_cost - impact_cost
    else:
        robust_variance = float(weights @ optimization_covariance @ weights)
        objective = (
            float(effective_alpha @ weights)
            - resolved_config.risk_aversion * robust_variance
            - linear_cost
            - impact_cost
        )

    return OptimizationResult(
        weights=pd.Series(weights, index=instruments, name="target_weight"),
        expected_return=expected_return,
        variance=variance,
        turnover=turnover(weights, current),
        linear_cost=linear_cost,
        impact_cost=impact_cost,
        objective=objective,
        iterations=iterations,
        converged=converged,
        factor_exposures=factor_exposure,
        objective_name=resolved_config.objective,
        tracking_error=reported_tracking_error,
        active_factor_exposures=active_factor_exposure,
        benchmark_expected_return=benchmark_expected_return,
        position_count=int(np.count_nonzero(weights > 1e-12)),
    )
