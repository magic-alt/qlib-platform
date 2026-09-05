from __future__ import annotations

import numpy as np
import pandas as pd


def nearest_psd(matrix: np.ndarray, *, floor: float = 1e-10) -> np.ndarray:
    symmetric = (matrix + matrix.T) / 2.0
    eigenvalues, eigenvectors = np.linalg.eigh(symmetric)
    return (eigenvectors * np.maximum(eigenvalues, floor)) @ eigenvectors.T


def estimate_covariance(
    returns: pd.DataFrame,
    *,
    shrinkage: float = 0.10,
    annualization: float = 252.0,
    min_observations: int = 20,
) -> pd.DataFrame:
    if not 0 <= shrinkage <= 1:
        raise ValueError("shrinkage must be in [0, 1]")
    if annualization <= 0:
        raise ValueError("annualization must be positive")
    numeric = returns.apply(pd.to_numeric, errors="coerce")
    if numeric.shape[0] < min_observations:
        raise ValueError("insufficient return history for covariance estimation")
    if numeric.columns.has_duplicates:
        raise ValueError("return columns must contain unique instruments")
    covariance = numeric.cov(min_periods=min_observations).to_numpy(dtype=float)
    if not np.isfinite(covariance).all():
        raise ValueError("covariance contains missing values; provide sufficient overlapping history")
    diagonal = np.diag(np.diag(covariance))
    shrunk = ((1.0 - shrinkage) * covariance + shrinkage * diagonal) * float(annualization)
    return pd.DataFrame(nearest_psd(shrunk), index=numeric.columns, columns=numeric.columns)
