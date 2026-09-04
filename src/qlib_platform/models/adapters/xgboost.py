from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from qlib_platform.models.base import ModelAdapter, RuntimeResolution


_DEFAULTS: dict[str, Any] = {
    "objective": "reg:squarederror",
    "eta": 0.03,
    "max_depth": 8,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "lambda": 50.0,
    "alpha": 10.0,
    "num_boost_round": 2000,
    "early_stopping_rounds": 100,
}


class ConfiguredXGBModel:
    """Qlib XGBModel with profile-owned fit controls."""

    def __init__(self, parameters: Mapping[str, Any]):
        from qlib.contrib.model.xgboost import XGBModel

        params = dict(parameters)
        self.num_boost_round = int(params.pop("num_boost_round", 2000))
        self.early_stopping_rounds = int(params.pop("early_stopping_rounds", 100))
        self.delegate = XGBModel(**params)

    @property
    def model(self) -> Any:
        return self.delegate.model

    @model.setter
    def model(self, value: Any) -> None:
        self.delegate.model = value

    def fit(self, dataset: Any, **kwargs: Any) -> Any:
        return self.delegate.fit(
            dataset,
            num_boost_round=int(kwargs.pop("num_boost_round", self.num_boost_round)),
            early_stopping_rounds=kwargs.pop("early_stopping_rounds", self.early_stopping_rounds),
            **kwargs,
        )

    def predict(self, dataset: Any, segment: str = "test") -> pd.Series:
        return self.delegate.predict(dataset, segment=segment)


class XGBoostAdapter(ModelAdapter):
    family = "xgboost"
    allowed_devices = frozenset({"auto", "cpu", "cuda"})

    def resolve_runtime(self, profile: Any, versions: Mapping[str, str]) -> RuntimeResolution:
        try:
            import xgboost as xgb
        except ImportError as exc:
            raise RuntimeError(
                "XGBoost profile selected but xgboost is not installed; install the xgboost extra"
            ) from exc
        resolved = dict(versions)
        resolved["xgboost"] = str(xgb.__version__)
        device = "cpu" if profile.device == "auto" else profile.device
        return RuntimeResolution(
            f"cuda:{profile.device_index}" if device == "cuda" else "cpu",
            "XGBoost auto uses CPU for deterministic portability" if profile.device == "auto" else None,
            resolved,
        )

    def parameters(
        self,
        profile: Any,
        resolved_device: str,
        *,
        feature_count: int,
        seed: int,
        num_threads: int,
    ) -> dict[str, Any]:
        del feature_count
        params = {**_DEFAULTS, "nthread": num_threads, **dict(profile.model_kwargs)}
        params["seed"] = seed
        params["device"] = resolved_device
        return params

    def build(self, parameters: Mapping[str, Any]) -> Any:
        return ConfiguredXGBModel(parameters)

    def save(self, model: Any, root: Path) -> str:
        if getattr(model, "model", None) is None:
            raise ValueError("XGBoost model is not fitted")
        name = "model.json"
        model.model.save_model(str(root / name))
        return name

    def scores(self, model: Any, features: pd.DataFrame) -> np.ndarray:
        import xgboost as xgb

        return np.asarray(model.model.predict(xgb.DMatrix(features)), dtype=float).reshape(-1)

    def load(
        self,
        root: Path,
        manifest: Mapping[str, Any],
        parameters: Mapping[str, Any],
        *,
        device: str,
    ) -> Any:
        del parameters, device
        import xgboost as xgb

        model = xgb.Booster()
        model.load_model(str(root / str(manifest["modelFile"])))
        return model

    def predict_loaded(self, model: Any, features: pd.DataFrame) -> np.ndarray:
        import xgboost as xgb

        return np.asarray(model.predict(xgb.DMatrix(features)), dtype=float).reshape(-1)

    def selected_training_steps(self, model: Any, parameters: Mapping[str, Any]) -> int:
        booster = getattr(model, "model", None)
        try:
            selected = int(getattr(booster, "best_iteration", -1)) + 1
        except (AttributeError, ValueError):
            selected = 0
        return selected if selected > 0 else int(parameters.get("num_boost_round", 1))

    def fit_final(self, model: Any, dataset: Any, selected_steps: int) -> None:
        import xgboost as xgb
        from qlib.data.dataset.handler import DataHandlerLP

        dataset.segments = {key: value for key, value in dataset.segments.items() if key != "valid"}
        frame = dataset.prepare("train", col_set=["feature", "label"], data_key=DataHandlerLP.DK_L).dropna()
        if frame.empty:
            raise ValueError("Empty data from dataset, please check your dataset config.")
        label = np.squeeze(frame["label"].to_numpy())
        model.model = xgb.train(
            dict(model.delegate._params),
            xgb.DMatrix(frame["feature"].to_numpy(), label=label),
            num_boost_round=selected_steps,
        )
