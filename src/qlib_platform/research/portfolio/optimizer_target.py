from __future__ import annotations

import pandas as pd

from qlib_platform.research.portfolio.optimizer import optimize_alpha_portfolio
from qlib_platform.research.portfolio.optimizer_constraints import OptimizationConstraints
from qlib_platform.research.portfolio.optimizer_types import OptimizationConfig, OptimizationResult


def optimized_target_portfolio(
    alpha: pd.Series,
    covariance: pd.DataFrame,
    *,
    current_weights: pd.Series | None = None,
    exposures: pd.DataFrame | None = None,
    constraints: OptimizationConstraints | None = None,
    config: OptimizationConfig | None = None,
    linear_costs: pd.Series | None = None,
    impact_coefficients: pd.Series | None = None,
) -> tuple[pd.DataFrame, OptimizationResult]:
    result = optimize_alpha_portfolio(
        alpha,
        covariance,
        current_weights=current_weights,
        exposures=exposures,
        constraints=constraints,
        config=config,
        linear_costs=linear_costs,
        impact_coefficients=impact_coefficients,
    )
    frame = pd.DataFrame(
        {
            "instrument": alpha.index.astype(str),
            "score": alpha.to_numpy(dtype=float),
            "target_weight": result.weights.to_numpy(dtype=float),
            "weighting": "alpha_risk_optimized",
            "pre_trade_turnover": result.turnover,
        }
    )
    return frame, result
