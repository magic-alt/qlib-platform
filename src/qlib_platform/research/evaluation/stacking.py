from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd

from qlib_platform.research.evaluation.ensemble import align_predictions


_REQUIRED_OOF_COLUMNS = ("fold_id", "is_oof", "fit_end", "prediction_time")


def validate_oof_metadata(metadata: pd.DataFrame, index: pd.Index) -> pd.DataFrame:
    """Validate temporal OOF provenance for leakage-sensitive stacking.

    `fit_end < prediction_time` is deliberately strict.  Equality is rejected so
    same-session labels/features cannot accidentally enter a model used to produce
    that row's prediction.
    """

    if metadata.index.has_duplicates:
        raise ValueError("OOF metadata contains duplicate index rows")
    missing_columns = [column for column in _REQUIRED_OOF_COLUMNS if column not in metadata.columns]
    if missing_columns:
        raise ValueError(f"OOF metadata missing required columns: {missing_columns}")
    if not metadata.index.equals(index):
        raise ValueError("OOF metadata index must exactly match prediction index")
    frame = metadata.loc[index, list(_REQUIRED_OOF_COLUMNS)].copy()
    if frame["fold_id"].isna().any():
        raise ValueError("OOF metadata contains missing fold_id")
    is_oof = frame["is_oof"].astype("boolean")
    if is_oof.isna().any() or not bool(is_oof.all()):
        raise ValueError("stacking fit accepts OOF predictions only")
    frame["fit_end"] = pd.to_datetime(frame["fit_end"], errors="coerce")
    frame["prediction_time"] = pd.to_datetime(frame["prediction_time"], errors="coerce")
    if frame[["fit_end", "prediction_time"]].isna().any().any():
        raise ValueError("OOF metadata contains invalid temporal provenance")
    leaked = frame["fit_end"] >= frame["prediction_time"]
    if leaked.any():
        example = frame.index[leaked][0]
        raise ValueError(f"stacking leakage detected: fit_end must precede prediction_time at {example!r}")
    return frame


@dataclass(frozen=True)
class StackingDiagnostics:
    n_rows: int
    n_models: int
    n_folds: int
    ridge_alpha: float
    first_prediction_time: str
    last_prediction_time: str


class StackingEnsembler:
    """Leakage-aware linear stacking over immutable OOF prediction streams.

    Base models must first generate predictions for samples excluded from their own
    fit.  The final meta model is fitted only on those OOF rows.  For evaluating the
    stack itself, use :meth:`temporal_cross_fit_predict`; it fits the meta learner on
    *earlier* OOF folds only, preventing meta-level look-ahead.
    """

    def __init__(self, *, ridge_alpha: float = 1e-6, fit_intercept: bool = True) -> None:
        if ridge_alpha < 0:
            raise ValueError("ridge_alpha must be non-negative")
        self.ridge_alpha = float(ridge_alpha)
        self.fit_intercept = bool(fit_intercept)
        self.model_names_: tuple[str, ...] | None = None
        self.coef_: np.ndarray | None = None
        self.intercept_: float = 0.0
        self.diagnostics_: StackingDiagnostics | None = None

    def _fit_matrix(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, float]:
        if self.fit_intercept:
            design = np.column_stack([np.ones(len(x), dtype=float), x])
            penalty = np.eye(design.shape[1], dtype=float) * self.ridge_alpha
            penalty[0, 0] = 0.0
        else:
            design = x
            penalty = np.eye(design.shape[1], dtype=float) * self.ridge_alpha
        lhs = design.T @ design + penalty
        rhs = design.T @ y
        try:
            params = np.linalg.solve(lhs, rhs)
        except np.linalg.LinAlgError:
            params = np.linalg.pinv(lhs) @ rhs
        if self.fit_intercept:
            return params[1:], float(params[0])
        return params, 0.0

    def fit(
        self,
        predictions: Mapping[str, pd.Series | pd.DataFrame],
        labels: pd.Series,
        metadata: pd.DataFrame,
    ) -> "StackingEnsembler":
        aligned = align_predictions(predictions)
        oof = validate_oof_metadata(metadata, aligned.index)
        if labels.index.has_duplicates or not labels.index.equals(aligned.index):
            raise ValueError("stacking labels must exactly match prediction index")
        y = pd.to_numeric(labels, errors="coerce")
        if y.isna().any():
            raise ValueError("stacking labels contain missing or non-numeric values")
        if len(aligned) <= aligned.shape[1]:
            raise ValueError("stacking requires more OOF rows than base models")
        self.coef_, self.intercept_ = self._fit_matrix(
            aligned.to_numpy(dtype=float), y.to_numpy(dtype=float)
        )
        self.model_names_ = tuple(str(name) for name in aligned.columns)
        self.diagnostics_ = StackingDiagnostics(
            n_rows=len(aligned),
            n_models=aligned.shape[1],
            n_folds=int(oof["fold_id"].nunique()),
            ridge_alpha=self.ridge_alpha,
            first_prediction_time=oof["prediction_time"].min().isoformat(),
            last_prediction_time=oof["prediction_time"].max().isoformat(),
        )
        return self

    def predict(self, predictions: Mapping[str, pd.Series | pd.DataFrame]) -> pd.Series:
        if self.coef_ is None or self.model_names_ is None:
            raise RuntimeError("stacking model is not fitted")
        aligned = align_predictions(predictions)
        if tuple(str(name) for name in aligned.columns) != self.model_names_:
            raise ValueError("stacking base-model order/names differ from fitted OOF streams")
        values = aligned.to_numpy(dtype=float) @ self.coef_ + self.intercept_
        return pd.Series(values, index=aligned.index, name="score")

    def temporal_cross_fit_predict(
        self,
        predictions: Mapping[str, pd.Series | pd.DataFrame],
        labels: pd.Series,
        metadata: pd.DataFrame,
        *,
        min_history_folds: int = 1,
    ) -> pd.Series:
        """Produce meta-level OOF predictions using earlier folds only.

        The earliest folds that lack `min_history_folds` predecessors remain NaN.
        This is intentional: filling them with an in-sample meta prediction would
        contaminate evaluation evidence.
        """

        if min_history_folds < 1:
            raise ValueError("min_history_folds must be at least one")
        aligned = align_predictions(predictions)
        oof = validate_oof_metadata(metadata, aligned.index)
        if not labels.index.equals(aligned.index):
            raise ValueError("stacking labels must exactly match prediction index")
        y = pd.to_numeric(labels, errors="coerce")
        if y.isna().any():
            raise ValueError("stacking labels contain missing or non-numeric values")

        fold_times = oof.groupby("fold_id", sort=False)["prediction_time"].min().sort_values()
        ordered_folds = list(fold_times.index)
        output = pd.Series(np.nan, index=aligned.index, dtype=float, name="score")
        for position, fold_id in enumerate(ordered_folds):
            if position < min_history_folds:
                continue
            train_folds = set(ordered_folds[:position])
            train_mask = oof["fold_id"].isin(train_folds)
            test_mask = oof["fold_id"] == fold_id
            current_start = oof.loc[test_mask, "prediction_time"].min()
            train_mask &= oof["prediction_time"] < current_start
            if int(train_mask.sum()) <= aligned.shape[1]:
                continue
            coef, intercept = self._fit_matrix(
                aligned.loc[train_mask].to_numpy(dtype=float), y.loc[train_mask].to_numpy(dtype=float)
            )
            output.loc[test_mask] = aligned.loc[test_mask].to_numpy(dtype=float) @ coef + intercept
        return output
