from __future__ import annotations

import numpy as np
import pandas as pd

from qlib_platform.research.portfolio.optimizer_constraints import (
    OptimizationConstraints,
    project_box_simplex,
)


def aligned_optional(
    series: pd.Series | None,
    index: pd.Index,
    default: float,
    name: str,
) -> np.ndarray:
    if series is None:
        return np.full(len(index), default, dtype=float)
    if not series.index.equals(index):
        raise ValueError(f"{name} must exactly match alpha index")
    values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError(f"{name} must be finite and non-negative")
    return values


def validate_inputs(alpha: pd.Series, covariance: pd.DataFrame) -> tuple[pd.Index, np.ndarray, np.ndarray]:
    if alpha.index.has_duplicates:
        raise ValueError("alpha index contains duplicate instruments")
    instruments = alpha.index
    if not covariance.index.equals(instruments) or not covariance.columns.equals(instruments):
        raise ValueError("covariance rows/columns must exactly match alpha index")
    alpha_values = pd.to_numeric(alpha, errors="coerce").to_numpy(dtype=float)
    covariance_values = covariance.to_numpy(dtype=float)
    if not np.isfinite(alpha_values).all() or not np.isfinite(covariance_values).all():
        raise ValueError("alpha/covariance contains invalid values")
    covariance_values = (covariance_values + covariance_values.T) / 2.0
    if float(np.linalg.eigvalsh(covariance_values).min()) < -1e-8:
        raise ValueError("covariance must be positive semidefinite")
    return instruments, alpha_values, covariance_values


def current_vector(
    current_weights: pd.Series | None,
    instruments: pd.Index,
    constraints: OptimizationConstraints,
) -> np.ndarray:
    if current_weights is None:
        return np.full(len(instruments), constraints.target_exposure / len(instruments), dtype=float)
    if not current_weights.index.equals(instruments):
        raise ValueError("current_weights must exactly match alpha index")
    current = pd.to_numeric(current_weights, errors="coerce").to_numpy(dtype=float)
    if not np.isfinite(current).all() or (current < -1e-12).any():
        raise ValueError("current_weights contains invalid values")
    if abs(float(current.sum()) - constraints.target_exposure) > 1e-6:
        current = project_box_simplex(
            current,
            total=constraints.target_exposure,
            lower=constraints.min_weight,
            upper=constraints.max_weight,
        )
    return current


def exposure_matrix(
    exposures: pd.DataFrame | None,
    instruments: pd.Index,
    constraints: OptimizationConstraints,
) -> tuple[np.ndarray | None, list[str]]:
    if exposures is None:
        return None, []
    if not exposures.index.equals(instruments):
        raise ValueError("factor exposures must exactly match alpha index")
    frame = exposures.apply(pd.to_numeric, errors="coerce")
    if frame.isna().any().any():
        raise ValueError("factor exposures contain invalid values")
    columns = [str(column) for column in frame.columns]
    unknown = set(constraints.factor_bounds) - set(columns)
    if unknown:
        raise ValueError(f"factor bounds reference unknown exposures: {sorted(unknown)}")
    return frame.to_numpy(dtype=float), columns
