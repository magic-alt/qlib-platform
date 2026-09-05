from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class OptimizationConstraints:
    target_exposure: float = 1.0
    min_weight: float = 0.0
    max_weight: float = 0.10
    max_turnover: float | None = None
    factor_bounds: Mapping[str, tuple[float, float]] = field(default_factory=dict)

    def validate(self, n_assets: int) -> None:
        if not 0 < self.target_exposure <= 1.0:
            raise ValueError("target_exposure must be in (0, 1]")
        if self.min_weight < 0 or self.max_weight <= 0 or self.min_weight > self.max_weight:
            raise ValueError("invalid weight bounds")
        if n_assets * self.min_weight - 1e-12 > self.target_exposure:
            raise ValueError("minimum weights make target exposure infeasible")
        if n_assets * self.max_weight + 1e-12 < self.target_exposure:
            raise ValueError("maximum weights make target exposure infeasible")
        if self.max_turnover is not None and self.max_turnover < 0:
            raise ValueError("max_turnover must be non-negative")
        for factor, bounds in self.factor_bounds.items():
            if len(bounds) != 2 or not np.isfinite(bounds).all() or bounds[0] > bounds[1]:
                raise ValueError(f"invalid exposure bounds for factor {factor!r}")


def project_box_simplex(values: np.ndarray, *, total: float, lower: float, upper: float) -> np.ndarray:
    if len(values) * lower > total + 1e-12 or len(values) * upper < total - 1e-12:
        raise ValueError("box constraints cannot satisfy target exposure")
    low = float(np.min(values - upper))
    high = float(np.max(values - lower))
    for _ in range(120):
        midpoint = (low + high) / 2.0
        projected = np.clip(values - midpoint, lower, upper)
        if float(projected.sum()) > total:
            low = midpoint
        else:
            high = midpoint
    return np.clip(values - (low + high) / 2.0, lower, upper)


def turnover(weights: np.ndarray, current: np.ndarray) -> float:
    return float(0.5 * np.abs(weights - current).sum())


def project_constraints(
    weights: np.ndarray,
    current: np.ndarray,
    constraints: OptimizationConstraints,
    exposures: np.ndarray | None,
    exposure_columns: list[str],
) -> np.ndarray:
    projected = project_box_simplex(
        weights,
        total=constraints.target_exposure,
        lower=constraints.min_weight,
        upper=constraints.max_weight,
    )
    for _ in range(160):
        before = projected.copy()
        if constraints.max_turnover is not None:
            current_turnover = turnover(projected, current)
            if current_turnover > constraints.max_turnover + 1e-12 and current_turnover > 0:
                alpha = constraints.max_turnover / current_turnover
                projected = project_box_simplex(
                    current + alpha * (projected - current),
                    total=constraints.target_exposure,
                    lower=constraints.min_weight,
                    upper=constraints.max_weight,
                )
        if exposures is not None:
            for column_index, factor in enumerate(exposure_columns):
                bounds = constraints.factor_bounds.get(factor)
                if bounds is None:
                    continue
                vector = exposures[:, column_index]
                value = float(projected @ vector)
                target = min(max(value, bounds[0]), bounds[1])
                delta = target - value
                if abs(delta) <= 1e-10:
                    continue
                direction = vector - float(vector.mean())
                denominator = float(direction @ vector)
                if abs(denominator) <= 1e-14:
                    raise ValueError(f"factor bound for {factor!r} is infeasible")
                projected = project_box_simplex(
                    projected + (delta / denominator) * direction,
                    total=constraints.target_exposure,
                    lower=constraints.min_weight,
                    upper=constraints.max_weight,
                )
        if float(np.max(np.abs(projected - before))) <= 1e-10:
            break
    if constraints.max_turnover is not None and turnover(projected, current) > constraints.max_turnover + 1e-7:
        raise ValueError("turnover constraint is infeasible")
    if exposures is not None:
        for column_index, factor in enumerate(exposure_columns):
            bounds = constraints.factor_bounds.get(factor)
            if bounds is None:
                continue
            actual = float(projected @ exposures[:, column_index])
            if actual < bounds[0] - 1e-6 or actual > bounds[1] + 1e-6:
                raise ValueError(f"factor exposure constraint is infeasible: {factor}={actual:.6f}")
    return projected
