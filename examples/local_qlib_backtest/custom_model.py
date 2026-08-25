"""Minimal custom Qlib model used by the local backtest example."""

from __future__ import annotations

from typing import Text, Union

import numpy as np
import pandas as pd
from qlib.data.dataset import DatasetH
from qlib.data.dataset.handler import DataHandlerLP
from qlib.data.dataset.weight import Reweighter
from qlib.model.base import Model
from sklearn.linear_model import Ridge


class WinsorizedRidgeModel(Model):
    """Ridge regression with train-label-derived prediction clipping.

    The clipping bounds are fitted on the train labels only. This class is intentionally
    small so it can be copied as the starting point for another sklearn-like estimator.
    """

    def __init__(
        self,
        alpha: float = 1.0,
        fit_intercept: bool = False,
        clip_quantile: float = 0.01,
    ) -> None:
        if alpha < 0:
            raise ValueError("alpha must be non-negative")
        if not 0 <= clip_quantile < 0.5:
            raise ValueError("clip_quantile must be in [0, 0.5)")
        self.alpha = float(alpha)
        self.fit_intercept = bool(fit_intercept)
        self.clip_quantile = float(clip_quantile)
        self.model: Ridge | None = None
        self.feature_columns: tuple[object, ...] | None = None
        self.lower_bound: float | None = None
        self.upper_bound: float | None = None

    def fit(self, dataset: DatasetH, reweighter: Reweighter | None = None) -> "WinsorizedRidgeModel":
        frame = dataset.prepare(
            "train",
            col_set=["feature", "label"],
            data_key=DataHandlerLP.DK_L,
        ).dropna()
        if frame.empty:
            raise ValueError("training data is empty after dropping missing values")

        features = frame["feature"]
        labels = np.asarray(frame["label"]).reshape(-1)
        if not np.isfinite(features.to_numpy()).all() or not np.isfinite(labels).all():
            raise ValueError("training data contains non-finite values")

        sample_weight = None
        if reweighter is not None:
            sample_weight = np.asarray(reweighter.reweight(frame)).reshape(-1)
            if len(sample_weight) != len(frame) or not np.isfinite(sample_weight).all():
                raise ValueError("reweighter returned invalid sample weights")

        self.model = Ridge(alpha=self.alpha, fit_intercept=self.fit_intercept, copy_X=False)
        self.model.fit(features, labels, sample_weight=sample_weight)
        self.feature_columns = tuple(features.columns)
        self.lower_bound = float(np.quantile(labels, self.clip_quantile))
        self.upper_bound = float(np.quantile(labels, 1.0 - self.clip_quantile))
        return self

    def predict(
        self,
        dataset: DatasetH,
        segment: Union[Text, slice] = "test",
    ) -> pd.Series:
        if (
            self.model is None
            or self.feature_columns is None
            or self.lower_bound is None
            or self.upper_bound is None
        ):
            raise ValueError("model is not fitted")

        features = dataset.prepare(segment, col_set="feature", data_key=DataHandlerLP.DK_I)
        if tuple(features.columns) != self.feature_columns:
            raise ValueError("prediction feature columns differ from fitted feature columns")
        if not np.isfinite(features.to_numpy()).all():
            raise ValueError("prediction data contains non-finite values")

        prediction = self.model.predict(features)
        prediction = np.clip(prediction, self.lower_bound, self.upper_bound)
        return pd.Series(prediction, index=features.index, name="score")
