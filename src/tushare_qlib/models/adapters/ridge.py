from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from ..base import ModelAdapter, RuntimeResolution


class _LoadedRidge:
    def __init__(self, coefficients: np.ndarray, intercept: float):
        self.coefficients = coefficients
        self.intercept = intercept

    def predict(self, values: np.ndarray) -> np.ndarray:
        return np.asarray(values @ self.coefficients + self.intercept, dtype=float)


class RidgeAdapter(ModelAdapter):
    family = "ridge"
    allowed_devices = frozenset({"auto", "cpu"})

    def resolve_runtime(self, profile: Any, versions: Mapping[str, str]) -> RuntimeResolution:
        import sklearn

        resolved = dict(versions)
        resolved["scikit-learn"] = str(sklearn.__version__)
        return RuntimeResolution("cpu", None, resolved)

    def parameters(
        self,
        profile: Any,
        resolved_device: str,
        *,
        feature_count: int,
        seed: int,
        num_threads: int,
    ) -> dict[str, Any]:
        del resolved_device, feature_count, seed, num_threads
        defaults = {
            "estimator": "ridge",
            "alpha": 1.0,
            "fit_intercept": False,
            "include_valid": False,
        }
        return {**defaults, **dict(profile.model_kwargs)}

    def build(self, parameters: Mapping[str, Any]) -> Any:
        from qlib.contrib.model.linear import LinearModel

        return LinearModel(**dict(parameters))

    def save(self, model: Any, root: Path) -> str:
        coefficients = getattr(model, "coef_", None)
        if coefficients is None:
            raise ValueError("Ridge model is not fitted")
        name = "model.npz"
        np.savez_compressed(
            root / name,
            coefficients=np.asarray(coefficients, dtype=np.float64),
            intercept=np.asarray(float(getattr(model, "intercept_", 0.0))),
        )
        return name

    def scores(self, model: Any, features: pd.DataFrame) -> np.ndarray:
        coefficients = getattr(model, "coef_", None)
        if coefficients is None:
            raise ValueError("Ridge model is not fitted")
        return np.asarray(
            features.to_numpy() @ np.asarray(coefficients) + float(getattr(model, "intercept_", 0.0)),
            dtype=float,
        ).reshape(-1)

    def load(
        self,
        root: Path,
        manifest: Mapping[str, Any],
        parameters: Mapping[str, Any],
        *,
        device: str,
    ) -> Any:
        del parameters, device
        state = np.load(root / str(manifest["modelFile"]))
        return _LoadedRidge(
            np.asarray(state["coefficients"], dtype=float),
            float(np.asarray(state["intercept"])),
        )

    def predict_loaded(self, model: Any, features: pd.DataFrame) -> np.ndarray:
        return np.asarray(model.predict(features.to_numpy()), dtype=float).reshape(-1)
