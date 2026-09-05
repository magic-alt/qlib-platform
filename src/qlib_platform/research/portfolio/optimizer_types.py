from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class OptimizationConfig:
    risk_aversion: float = 5.0
    linear_turnover_cost: float = 0.001
    impact_cost: float = 0.01
    step_size: float = 0.05
    max_iterations: int = 2_000
    tolerance: float = 1e-8

    def validate(self) -> None:
        if self.risk_aversion < 0 or self.linear_turnover_cost < 0 or self.impact_cost < 0:
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
