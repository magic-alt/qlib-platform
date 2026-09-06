from __future__ import annotations

import pandas as pd

from qlib_platform.research.portfolio.implementation import round_weights_to_lots
from qlib_platform.research.portfolio.optimizer import optimize_alpha_portfolio
from qlib_platform.research.portfolio.optimizer_constraints import OptimizationConstraints
from qlib_platform.research.portfolio.optimizer_types import OptimizationConfig, OptimizationResult


def optimized_target_portfolio(
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
    prices: pd.Series | None = None,
    portfolio_value: float | None = None,
    lot_sizes: int | pd.Series = 100,
) -> tuple[pd.DataFrame, OptimizationResult]:
    result = optimize_alpha_portfolio(
        alpha,
        covariance,
        current_weights=current_weights,
        benchmark_weights=benchmark_weights,
        exposures=exposures,
        constraints=constraints,
        config=config,
        linear_costs=linear_costs,
        impact_coefficients=impact_coefficients,
        alpha_uncertainty=alpha_uncertainty,
        risk_budgets=risk_budgets,
    )
    frame = pd.DataFrame(
        {
            "instrument": alpha.index.astype(str),
            "score": alpha.to_numpy(dtype=float),
            "target_weight": result.weights.to_numpy(dtype=float),
            "weighting": result.objective_name,
            "pre_trade_turnover": result.turnover,
        }
    )
    if (prices is None) != (portfolio_value is None):
        raise ValueError("prices and portfolio_value must be supplied together for lot sizing")
    if prices is not None and portfolio_value is not None:
        implementation = round_weights_to_lots(
            result.weights,
            prices,
            portfolio_value=portfolio_value,
            lot_sizes=lot_sizes,
        )
        frame["target_shares"] = implementation.shares.to_numpy(dtype="int64")
        frame["implemented_weight"] = implementation.weights.to_numpy(dtype=float)
        frame.attrs["invested_value"] = implementation.invested_value
        frame.attrs["residual_cash"] = implementation.residual_cash
    return frame, result
