from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

import pandas as pd


OptimizationObjective = Literal[
    "alpha_risk",
    "benchmark_relative",
    "minimum_variance",
    "risk_parity",
]


@dataclass(frozen=True)
class OptimizationConfig:
    objective: OptimizationObjective = "alpha_risk"
    risk_aversion: float = 5.0
    linear_turnover_cost: float = 0.001
    impact_cost: float = 0.01
    robust_alpha_uncertainty_penalty: float = 0.0
    covariance_diagonal_buffer: float = 0.0
    step_size: float = 0.05
    max_iterations: int = 2_000
    tolerance: float = 1e-8

    def validate(self) -> None:
        if self.objective not in {
            "alpha_risk",
            "benchmark_relative",
            "minimum_variance",
            "risk_parity",
        }:
            raise ValueError(f"unsupported optimization objective: {self.objective!r}")
        if (
            self.risk_aversion < 0
            or self.linear_turnover_cost < 0
            or self.impact_cost < 0
            or self.robust_alpha_uncertainty_penalty < 0
            or self.covariance_diagonal_buffer < 0
        ):
            raise ValueError("optimizer penalties must be non-negative")
        if self.step_size <= 0 or self.max_iterations <= 0 or self.tolerance <= 0:
            raise ValueError("optimizer iteration settings must be positive")


@dataclass(frozen=True)
class OptimizationResult:
    weights: pd.Series
    expected_return: float
    variance: float
    turnover: float
    linear_cost: float
    impact_cost: float
    objective: float
    iterations: int
    converged: bool
    factor_exposures: Mapping[str, float]
    objective_name: OptimizationObjective = "alpha_risk"
    tracking_error: float | None = None
    active_factor_exposures: Mapping[str, float] | None = None
    benchmark_expected_return: float | None = None
    position_count: int = 0
