from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from qlib_platform.models.base import ModelAdapter, RuntimeResolution


@dataclass
class _TemporalQlibModel:
    """Adapt a normal DatasetH into Qlib's TSDatasetH without changing its handler or split dates."""

    model: Any
    step_len: int

    def _dataset(self, dataset: Any) -> Any:
        from qlib.data.dataset import TSDatasetH

        segments = dict(dataset.segments)
        return TSDatasetH(handler=dataset.handler, segments=segments, step_len=self.step_len)

    def fit(self, dataset: Any, *args: Any, **kwargs: Any) -> Any:
        return self.model.fit(self._dataset(dataset), *args, **kwargs)

    def predict(self, dataset: Any, *args: Any, **kwargs: Any) -> Any:
        return self.model.predict(self._dataset(dataset), *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        model = object.__getattribute__(self, "model")
        return getattr(model, name)


class _ResearchOnlyMixin:
    deployment_capable = False

    @staticmethod
    def _deployment_error() -> RuntimeError:
        return RuntimeError(
            "this Qlib model-zoo adapter is research-only: portable live model bundles "
            "require a model-specific sequence/preprocessing contract and are intentionally "
            "blocked until that contract is implemented"
        )

    def save(self, model: Any, root: Path) -> str:
        del model, root
        raise self._deployment_error()

    def scores(self, model: Any, features: pd.DataFrame) -> np.ndarray:
        del model, features
        raise self._deployment_error()

    def load(
        self,
        root: Path,
        manifest: Mapping[str, Any],
        parameters: Mapping[str, Any],
        *,
        device: str,
    ) -> Any:
        del root, manifest, parameters, device
        raise self._deployment_error()

    def predict_loaded(self, model: Any, features: pd.DataFrame) -> np.ndarray:
        del model, features
        raise self._deployment_error()

    def fit_final(self, model: Any, dataset: Any, selected_steps: int) -> None:
        del model, dataset, selected_steps
        raise self._deployment_error()


class QlibTorchResearchAdapter(_ResearchOnlyMixin, ModelAdapter):
    """Base adapter for upstream Qlib PyTorch research models.

    Temporal models use Qlib's own TSDatasetH over the *same* platform handler and
    train/valid/test date boundaries.  No synthetic sequence reshaping is performed.
    """

    upstream_module: str
    upstream_class: str
    temporal: bool = False
    step_len: int = 20
    auto_d_feat: bool = True
    supports_n_jobs: bool = True
    allowed_devices = frozenset({"auto", "cpu", "cuda"})
    parity_tolerance = 1e-5

    def resolve_runtime(self, profile: Any, versions: Mapping[str, str]) -> RuntimeResolution:
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError(f"{self.family} requires PyTorch; install the pytorch extra") from exc

        resolved = dict(versions)
        resolved["torch"] = str(torch.__version__)
        cuda_available = bool(torch.cuda.is_available())
        if profile.device == "cpu":
            return RuntimeResolution("cpu", None, resolved)
        if profile.device == "cuda":
            if not cuda_available:
                raise RuntimeError(f"{self.family} requested CUDA but torch.cuda.is_available() is false")
            name = torch.cuda.get_device_name(profile.device_index)
            return RuntimeResolution(f"cuda:{profile.device_index}", None, resolved, str(name))
        if cuda_available:
            name = torch.cuda.get_device_name(profile.device_index)
            return RuntimeResolution(f"cuda:{profile.device_index}", None, resolved, str(name))
        return RuntimeResolution(
            "cpu",
            "Qlib upstream PyTorch models expose CUDA/CPU only; using CPU",
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
        params = dict(profile.model_kwargs)
        if self.auto_d_feat:
            configured = params.get("d_feat")
            if configured is not None and int(configured) != feature_count:
                raise ValueError(
                    f"{self.family} d_feat={configured} does not match the pinned "
                    f"FeatureSnapshot width {feature_count}; changing feature width per model "
                    "would break fair-comparison semantics"
                )
            params["d_feat"] = feature_count
        params["GPU"] = profile.device_index if resolved_device.startswith("cuda") else -1
        params.setdefault("seed", seed)
        if self.supports_n_jobs:
            params.setdefault("n_jobs", num_threads)
        if self.temporal:
            configured_step = int(params.pop("step_len", self.step_len))
            if configured_step < 2:
                raise ValueError("temporal Qlib model step_len must be at least 2")
            params["_qlib_platform_step_len"] = configured_step
        return params

    def build(self, parameters: Mapping[str, Any]) -> Any:
        kwargs = dict(parameters)
        step_len = int(kwargs.pop("_qlib_platform_step_len", self.step_len))
        module = importlib.import_module(self.upstream_module)
        model_class = getattr(module, self.upstream_class)
        model = model_class(**kwargs)
        return _TemporalQlibModel(model, step_len) if self.temporal else model


class QlibLSTMAdapter(QlibTorchResearchAdapter):
    family = "qlib_lstm"
    upstream_module = "qlib.contrib.model.pytorch_lstm_ts"
    upstream_class = "LSTM"
    temporal = True


class QlibGRUAdapter(QlibTorchResearchAdapter):
    family = "qlib_gru"
    upstream_module = "qlib.contrib.model.pytorch_gru_ts"
    upstream_class = "GRU"
    temporal = True


class QlibTransformerAdapter(QlibTorchResearchAdapter):
    family = "qlib_transformer"
    upstream_module = "qlib.contrib.model.pytorch_transformer_ts"
    upstream_class = "TransformerModel"
    temporal = True


class QlibTCNAdapter(QlibTorchResearchAdapter):
    family = "qlib_tcn"
    upstream_module = "qlib.contrib.model.pytorch_tcn_ts"
    upstream_class = "TCN"
    temporal = True


class QlibTabNetAdapter(QlibTorchResearchAdapter):
    family = "qlib_tabnet"
    supports_n_jobs = False
    upstream_module = "qlib.contrib.model.pytorch_tabnet"
    upstream_class = "TabnetModel"
    temporal = False


class QlibDoubleEnsembleAdapter(_ResearchOnlyMixin, ModelAdapter):
    family = "qlib_double_ensemble"
    allowed_devices = frozenset({"auto", "cpu"})

    def resolve_runtime(self, profile: Any, versions: Mapping[str, str]) -> RuntimeResolution:
        try:
            import lightgbm as lgb
        except ImportError as exc:
            raise RuntimeError("DoubleEnsemble requires LightGBM") from exc
        resolved = dict(versions)
        resolved["lightgbm"] = str(lgb.__version__)
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
        del resolved_device, feature_count
        params = dict(profile.model_kwargs)
        params.setdefault("seed", seed)
        params.setdefault("feature_fraction_seed", seed)
        params.setdefault("bagging_seed", seed)
        params.setdefault("num_threads", num_threads)
        return params

    def build(self, parameters: Mapping[str, Any]) -> Any:
        from qlib.contrib.model.double_ensemble import DEnsembleModel

        return DEnsembleModel(**dict(parameters))
