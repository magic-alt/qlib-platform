from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd


def _as_series(value: pd.Series | pd.DataFrame, *, name: str) -> pd.Series:
    if isinstance(value, pd.Series):
        series = value.copy()
    elif isinstance(value, pd.DataFrame):
        if "score" in value.columns:
            series = value["score"].copy()
        elif value.shape[1] == 1:
            series = value.iloc[:, 0].copy()
        else:
            raise ValueError(f"prediction {name!r} must contain one score column")
    else:  # pragma: no cover - guarded by typing, retained for fail-closed runtime use
        raise TypeError(f"prediction {name!r} must be a pandas Series or DataFrame")
    if series.index.has_duplicates:
        raise ValueError(f"prediction {name!r} contains duplicate index rows")
    series = pd.to_numeric(series, errors="coerce")
    if series.isna().any():
        raise ValueError(f"prediction {name!r} contains missing or non-numeric scores")
    series.name = name
    return series.astype(float)


def align_predictions(predictions: Mapping[str, pd.Series | pd.DataFrame]) -> pd.DataFrame:
    """Align base-model predictions without silently imputing or dropping rows.

    Ensemble inputs are treated as immutable OOS evidence.  Every model must cover
    exactly the same index; an inner join would otherwise hide missing predictions
    and change the experiment population.
    """

    if len(predictions) < 2:
        raise ValueError("an ensemble requires at least two prediction streams")
    series = [_as_series(value, name=str(name)) for name, value in predictions.items()]
    reference = series[0].index
    for item in series[1:]:
        if not item.index.equals(reference):
            missing = reference.difference(item.index)
            extra = item.index.difference(reference)
            raise ValueError(
                "ensemble prediction index mismatch: "
                f"model={item.name!r}, missing={len(missing)}, extra={len(extra)}"
            )
    return pd.concat(series, axis=1)


def average_predictions(predictions: Mapping[str, pd.Series | pd.DataFrame]) -> pd.Series:
    aligned = align_predictions(predictions)
    result = aligned.mean(axis=1)
    result.name = "score"
    return result


def weighted_blend(
    predictions: Mapping[str, pd.Series | pd.DataFrame],
    weights: Mapping[str, float] | Sequence[float],
) -> pd.Series:
    aligned = align_predictions(predictions)
    if isinstance(weights, Mapping):
        if set(weights) != set(aligned.columns):
            raise ValueError("weight keys must exactly match ensemble model names")
        vector = np.asarray([float(weights[name]) for name in aligned.columns], dtype=float)
    else:
        vector = np.asarray(list(weights), dtype=float)
        if vector.shape != (aligned.shape[1],):
            raise ValueError("weight vector length must match the number of models")
    if not np.isfinite(vector).all() or (vector < 0).any():
        raise ValueError("ensemble weights must be finite and non-negative")
    total = float(vector.sum())
    if total <= 0:
        raise ValueError("ensemble weights must sum to a positive value")
    vector /= total
    result = pd.Series(aligned.to_numpy(dtype=float) @ vector, index=aligned.index, name="score")
    return result


@dataclass(frozen=True)
class DoubleEnsembleConfig:
    """Stable adapter configuration for Qlib's DEnsembleModel."""

    base_model: str = "gbm"
    loss: str = "mse"
    num_models: int = 6
    enable_sr: bool = True
    enable_fs: bool = True
    alpha1: float = 1.0
    alpha2: float = 1.0
    bins_sr: int = 10
    bins_fs: int = 5
    epochs: int = 100
    early_stopping_rounds: int | None = None

    def validate(self) -> None:
        if self.base_model != "gbm":
            raise ValueError("qlib-platform currently certifies DoubleEnsemble with base_model='gbm' only")
        if self.loss != "mse":
            raise ValueError("Qlib DEnsembleModel currently supports mse loss in this adapter")
        if self.num_models < 2:
            raise ValueError("DoubleEnsemble requires at least two sub-models")
        if self.bins_sr < 2 or self.bins_fs < 2:
            raise ValueError("DoubleEnsemble bin counts must be at least two")
        if self.epochs <= 0:
            raise ValueError("DoubleEnsemble epochs must be positive")


def build_qlib_double_ensemble(
    config: DoubleEnsembleConfig | None = None,
    **lightgbm_params: Any,
) -> Any:
    """Create Qlib's DoubleEnsemble lazily without making Qlib a core dependency."""

    resolved = config or DoubleEnsembleConfig()
    resolved.validate()
    try:
        from qlib.contrib.model.double_ensemble import DEnsembleModel
    except ImportError as exc:  # pragma: no cover - optional qlib/lightgbm dependency
        raise RuntimeError(
            "DoubleEnsemble requires the qlib extra: install qlib-platform[qlib]"
        ) from exc
    kwargs = asdict(resolved)
    kwargs.update(lightgbm_params)
    return DEnsembleModel(**kwargs)
