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
    max_trade_cost: float | None = None
    max_tracking_error: float | None = None
    factor_bounds: Mapping[str, tuple[float, float]] = field(default_factory=dict)
    active_factor_bounds: Mapping[str, tuple[float, float]] = field(default_factory=dict)
    max_positions: int | None = None
    min_position_weight: float | None = None

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
        if self.max_trade_cost is not None and self.max_trade_cost < 0:
            raise ValueError("max_trade_cost must be non-negative")
        if self.max_tracking_error is not None and self.max_tracking_error < 0:
            raise ValueError("max_tracking_error must be non-negative")
        if self.max_positions is not None:
            if self.max_positions <= 0 or self.max_positions > n_assets:
                raise ValueError("max_positions must be in [1, n_assets]")
            if self.max_positions * self.max_weight + 1e-12 < self.target_exposure:
                raise ValueError("max_positions and max_weight make target exposure infeasible")
        if self.min_position_weight is not None:
            if self.min_position_weight <= 0 or self.min_position_weight > self.max_weight:
                raise ValueError("min_position_weight must be in (0, max_weight]")
            if self.min_position_weight < self.min_weight:
                raise ValueError("min_position_weight cannot be below min_weight")
        for bounds_by_factor in (self.factor_bounds, self.active_factor_bounds):
            for factor, bounds in bounds_by_factor.items():
                if len(bounds) != 2 or not np.isfinite(bounds).all() or bounds[0] > bounds[1]:
                    raise ValueError(f"invalid exposure bounds for factor {factor!r}")


def _bound_array(value: float | np.ndarray, n_assets: int, *, name: str) -> np.ndarray:
    array: np.ndarray = np.asarray(value, dtype=float)
    if array.ndim == 0:
        result: np.ndarray = np.full(n_assets, float(array.item()), dtype=float)
    else:
        result = array
        if result.shape != (n_assets,):
            raise ValueError(f"{name} must be scalar or length n_assets")
    if not np.isfinite(result).all():
        raise ValueError(f"{name} must contain finite values")
    return result


def project_box_simplex(
    values: np.ndarray,
    *,
    total: float,
    lower: float | np.ndarray,
    upper: float | np.ndarray,
) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    lower_values = _bound_array(lower, len(values), name="lower")
    upper_values = _bound_array(upper, len(values), name="upper")
    if np.any(lower_values > upper_values):
        raise ValueError("lower bounds cannot exceed upper bounds")
    if float(lower_values.sum()) > total + 1e-12 or float(upper_values.sum()) < total - 1e-12:
        raise ValueError("box constraints cannot satisfy target exposure")
    low = float(np.min(values - upper_values))
    high = float(np.max(values - lower_values))
    for _ in range(120):
        midpoint = (low + high) / 2.0
        projected = np.clip(values - midpoint, lower_values, upper_values)
        if float(projected.sum()) > total:
            low = midpoint
        else:
            high = midpoint
    return np.clip(values - (low + high) / 2.0, lower_values, upper_values)


def turnover(weights: np.ndarray, current: np.ndarray) -> float:
    return float(0.5 * np.abs(weights - current).sum())


def trade_cost(
    weights: np.ndarray,
    current: np.ndarray,
    linear_costs: np.ndarray,
    impact_coefficients: np.ndarray,
) -> float:
    delta = weights - current
    return float(linear_costs @ np.abs(delta) + impact_coefficients @ (delta**2))


def tracking_error(weights: np.ndarray, benchmark: np.ndarray, covariance: np.ndarray) -> float:
    active = weights - benchmark
    variance = float(active @ covariance @ active)
    return float(np.sqrt(max(0.0, variance)))


def _trade_budget_scale(linear_part: float, quadratic_part: float, budget: float) -> float:
    if linear_part + quadratic_part <= budget + 1e-15:
        return 1.0
    if budget <= 0:
        return 0.0
    if quadratic_part <= 1e-18:
        return min(1.0, budget / max(linear_part, 1e-18))
    discriminant = linear_part**2 + 4.0 * quadratic_part * budget
    root = (-linear_part + float(np.sqrt(max(0.0, discriminant)))) / (2.0 * quadratic_part)
    return min(1.0, max(0.0, root))


def _project_factor_bound(
    projected: np.ndarray,
    vector: np.ndarray,
    bounds: tuple[float, float],
    *,
    offset: float,
    total: float,
    lower: np.ndarray,
    upper: np.ndarray,
) -> np.ndarray:
    value = float(projected @ vector - offset)
    target = min(max(value, bounds[0]), bounds[1])
    delta = target - value
    if abs(delta) <= 1e-10:
        return projected
    free = upper > lower + 1e-14
    if not bool(np.any(free)):
        raise ValueError("factor bound is infeasible under fixed support")
    direction = np.zeros_like(vector)
    free_vector = vector[free]
    direction[free] = free_vector - float(free_vector.mean())
    denominator = float(direction @ vector)
    if abs(denominator) <= 1e-14:
        raise ValueError("factor bound is infeasible")
    return project_box_simplex(
        projected + (delta / denominator) * direction,
        total=total,
        lower=lower,
        upper=upper,
    )


def project_constraints(
    weights: np.ndarray,
    current: np.ndarray,
    constraints: OptimizationConstraints,
    exposures: np.ndarray | None,
    exposure_columns: list[str],
    *,
    benchmark: np.ndarray | None = None,
    covariance: np.ndarray | None = None,
    linear_costs: np.ndarray | None = None,
    impact_coefficients: np.ndarray | None = None,
    support_mask: np.ndarray | None = None,
) -> np.ndarray:
    n_assets = len(weights)
    lower = np.full(n_assets, constraints.min_weight, dtype=float)
    upper = np.full(n_assets, constraints.max_weight, dtype=float)
    if support_mask is not None:
        support = np.asarray(support_mask, dtype=bool)
        if support.shape != (n_assets,):
            raise ValueError("support_mask must be length n_assets")
        lower[~support] = 0.0
        upper[~support] = 0.0
        if constraints.min_position_weight is not None:
            lower[support] = np.maximum(lower[support], constraints.min_position_weight)
    projected = project_box_simplex(
        weights,
        total=constraints.target_exposure,
        lower=lower,
        upper=upper,
    )

    needs_benchmark = constraints.max_tracking_error is not None or bool(constraints.active_factor_bounds)
    if needs_benchmark and benchmark is None:
        raise ValueError("benchmark_weights are required for active-risk constraints")
    if constraints.max_tracking_error is not None and covariance is None:
        raise ValueError("covariance is required for tracking-error constraints")
    if constraints.max_trade_cost is not None and (linear_costs is None or impact_coefficients is None):
        raise ValueError("cost vectors are required for max_trade_cost")

    benchmark_factor_values: dict[str, float] = {}
    if benchmark is not None and exposures is not None:
        benchmark_factor_values = {
            factor: float(benchmark @ exposures[:, position])
            for position, factor in enumerate(exposure_columns)
        }

    for _ in range(200):
        before = projected.copy()
        if constraints.max_turnover is not None:
            current_turnover = turnover(projected, current)
            if current_turnover > constraints.max_turnover + 1e-12 and current_turnover > 0:
                alpha = constraints.max_turnover / current_turnover
                projected = project_box_simplex(
                    current + alpha * (projected - current),
                    total=constraints.target_exposure,
                    lower=lower,
                    upper=upper,
                )
        if constraints.max_trade_cost is not None:
            assert linear_costs is not None
            assert impact_coefficients is not None
            delta = projected - current
            linear_part = float(linear_costs @ np.abs(delta))
            quadratic_part = float(impact_coefficients @ (delta**2))
            alpha = _trade_budget_scale(
                linear_part,
                quadratic_part,
                constraints.max_trade_cost,
            )
            if alpha < 1.0 - 1e-12:
                projected = project_box_simplex(
                    current + alpha * delta,
                    total=constraints.target_exposure,
                    lower=lower,
                    upper=upper,
                )
        if exposures is not None:
            for column_index, factor in enumerate(exposure_columns):
                vector = exposures[:, column_index]
                bounds = constraints.factor_bounds.get(factor)
                if bounds is not None:
                    try:
                        projected = _project_factor_bound(
                            projected,
                            vector,
                            bounds,
                            offset=0.0,
                            total=constraints.target_exposure,
                            lower=lower,
                            upper=upper,
                        )
                    except ValueError as exc:
                        raise ValueError(f"factor bound for {factor!r} is infeasible") from exc
                active_bounds = constraints.active_factor_bounds.get(factor)
                if active_bounds is not None:
                    try:
                        projected = _project_factor_bound(
                            projected,
                            vector,
                            active_bounds,
                            offset=benchmark_factor_values[factor],
                            total=constraints.target_exposure,
                            lower=lower,
                            upper=upper,
                        )
                    except ValueError as exc:
                        raise ValueError(f"active factor bound for {factor!r} is infeasible") from exc
        if constraints.max_tracking_error is not None:
            assert benchmark is not None
            assert covariance is not None
            current_tracking_error = tracking_error(projected, benchmark, covariance)
            if current_tracking_error > constraints.max_tracking_error + 1e-12:
                if current_tracking_error <= 0:
                    raise ValueError("tracking-error constraint is infeasible")
                alpha = constraints.max_tracking_error / current_tracking_error
                projected = project_box_simplex(
                    benchmark + alpha * (projected - benchmark),
                    total=constraints.target_exposure,
                    lower=lower,
                    upper=upper,
                )
        if float(np.max(np.abs(projected - before))) <= 1e-10:
            break

    if (
        constraints.max_turnover is not None
        and turnover(projected, current) > constraints.max_turnover + 1e-7
    ):
        raise ValueError("turnover constraint is infeasible")
    if constraints.max_trade_cost is not None:
        assert linear_costs is not None
        assert impact_coefficients is not None
        actual_cost = trade_cost(projected, current, linear_costs, impact_coefficients)
        if actual_cost > constraints.max_trade_cost + 1e-8:
            raise ValueError("transaction-cost budget is infeasible")
    if constraints.max_tracking_error is not None:
        assert benchmark is not None
        assert covariance is not None
        actual_tracking_error = tracking_error(projected, benchmark, covariance)
        if actual_tracking_error > constraints.max_tracking_error + 1e-7:
            raise ValueError("tracking-error constraint is infeasible")
    if exposures is not None:
        for column_index, factor in enumerate(exposure_columns):
            actual = float(projected @ exposures[:, column_index])
            bounds = constraints.factor_bounds.get(factor)
            if bounds is not None and (actual < bounds[0] - 1e-6 or actual > bounds[1] + 1e-6):
                raise ValueError(f"factor exposure constraint is infeasible: {factor}={actual:.6f}")
            active_bounds = constraints.active_factor_bounds.get(factor)
            if active_bounds is not None:
                active = actual - benchmark_factor_values[factor]
                if active < active_bounds[0] - 1e-6 or active > active_bounds[1] + 1e-6:
                    raise ValueError(
                        f"active factor exposure constraint is infeasible: {factor}={active:.6f}"
                    )
    return projected


def apply_position_constraints(
    weights: np.ndarray,
    current: np.ndarray,
    constraints: OptimizationConstraints,
    exposures: np.ndarray | None,
    exposure_columns: list[str],
    *,
    benchmark: np.ndarray | None = None,
    covariance: np.ndarray | None = None,
    linear_costs: np.ndarray | None = None,
    impact_coefficients: np.ndarray | None = None,
) -> np.ndarray:
    if constraints.max_positions is None and constraints.min_position_weight is None:
        return weights

    min_required = int(np.ceil(constraints.target_exposure / constraints.max_weight - 1e-12))
    max_allowed = constraints.max_positions or len(weights)
    if constraints.min_position_weight is not None:
        max_by_minimum = int(np.floor(constraints.target_exposure / constraints.min_position_weight + 1e-12))
        max_allowed = min(max_allowed, max_by_minimum)
    if max_allowed < min_required:
        raise ValueError("position constraints cannot satisfy target exposure")

    if constraints.min_position_weight is None:
        desired_count = max_allowed
    else:
        desired_count = int(np.count_nonzero(weights >= constraints.min_position_weight - 1e-12))
        desired_count = min(max_allowed, max(min_required, desired_count))
    order = np.argsort(-weights, kind="stable")
    support = np.zeros(len(weights), dtype=bool)
    support[order[:desired_count]] = True

    projected = project_constraints(
        weights,
        current,
        constraints,
        exposures,
        exposure_columns,
        benchmark=benchmark,
        covariance=covariance,
        linear_costs=linear_costs,
        impact_coefficients=impact_coefficients,
        support_mask=support,
    )
    if constraints.max_positions is not None:
        count = int(np.count_nonzero(projected > 1e-12))
        if count > constraints.max_positions:
            raise ValueError("cardinality constraint is infeasible")
    if constraints.min_position_weight is not None:
        active = projected > 1e-12
        if bool(np.any(projected[active] < constraints.min_position_weight - 1e-7)):
            raise ValueError("minimum-position constraint is infeasible")
    return projected
