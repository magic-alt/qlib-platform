from __future__ import annotations

import numpy as np
import pandas as pd

from qlib_platform.research.portfolio.optimizer_constraints import (
    OptimizationConstraints,
    project_constraints,
    turnover,
)
from qlib_platform.research.portfolio.optimizer_inputs import (
    aligned_optional,
    current_vector,
    exposure_matrix,
    validate_inputs,
)
from qlib_platform.research.portfolio.optimizer_types import OptimizationConfig, OptimizationResult


def optimize_alpha_portfolio(
    alpha: pd.Series,
    covariance: pd.DataFrame,
    *,
    current_weights: pd.Series | None = None,
    exposures: pd.DataFrame | None = None,
    constraints: OptimizationConstraints | None = None,
    config: OptimizationConfig | None = None,
    linear_costs: pd.Series | None = None,
    impact_coefficients: pd.Series | None = None,
) -> OptimizationResult:
    instruments, alpha_values, covariance_values = validate_inputs(alpha, covariance)
    resolved_constraints = constraints or OptimizationConstraints()
    resolved_config = config or OptimizationConfig()
    resolved_constraints.validate(len(instruments))
    resolved_config.validate()
    current = current_vector(current_weights, instruments, resolved_constraints)
    exposure_values, exposure_columns = exposure_matrix(exposures, instruments, resolved_constraints)
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
    weights = project_constraints(
        current.copy(), current, resolved_constraints, exposure_values, exposure_columns
    )
    converged = False
    iterations = 0
    for iteration in range(resolved_config.max_iterations):
        delta = weights - current
        gradient = (
            -alpha_values
            + 2.0 * resolved_config.risk_aversion * (covariance_values @ weights)
            + linear * np.sign(delta)
            + 2.0 * impact * delta
        )
        candidate = project_constraints(
            weights - resolved_config.step_size / np.sqrt(iteration + 1.0) * gradient,
            current,
            resolved_constraints,
            exposure_values,
            exposure_columns,
        )
        iterations = iteration + 1
        if float(np.max(np.abs(candidate - weights))) <= resolved_config.tolerance:
            weights = candidate
            converged = True
            break
        weights = candidate
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
    return OptimizationResult(
        weights=pd.Series(weights, index=instruments, name="target_weight"),
        expected_return=expected_return,
        variance=variance,
        turnover=turnover(weights, current),
        linear_cost=linear_cost,
        impact_cost=impact_cost,
        objective=(expected_return - resolved_config.risk_aversion * variance - linear_cost - impact_cost),
        iterations=iterations,
        converged=converged,
        factor_exposures=factor_exposure,
    )
