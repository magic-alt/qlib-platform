from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from qlib_platform.models.base import ModelAdapter, RuntimeResolution


class PyTorchDNNAdapter(ModelAdapter):
    family = "pytorch_dnn"
    allowed_devices = frozenset({"auto", "cpu", "cuda", "mps"})
    parity_tolerance = 1e-5

    def resolve_runtime(self, profile: Any, versions: Mapping[str, str]) -> RuntimeResolution:
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError(
                "PyTorch profile selected but torch is not installed; install the pytorch extra"
            ) from exc
        resolved = dict(versions)
        resolved["torch"] = str(torch.__version__)
        cuda_available = bool(torch.cuda.is_available())
        mps_backend = getattr(torch.backends, "mps", None)
        mps_available = bool(mps_backend and mps_backend.is_built() and mps_backend.is_available())
        if profile.device == "cpu":
            return RuntimeResolution("cpu", None, resolved)
        if profile.device == "cuda":
            if not cuda_available:
                raise RuntimeError(
                    "PyTorch CUDA was explicitly requested but torch.cuda.is_available() is false"
                )
            return RuntimeResolution(f"cuda:{profile.device_index}", None, resolved)
        if profile.device == "mps":
            if not mps_available:
                raise RuntimeError(
                    "PyTorch MPS was explicitly requested but the MPS backend is not available"
                )
            return RuntimeResolution("mps", None, resolved)
        if cuda_available:
            return RuntimeResolution(f"cuda:{profile.device_index}", None, resolved)
        if mps_available:
            return RuntimeResolution("mps", None, resolved)
        return RuntimeResolution("cpu", "No PyTorch CUDA or MPS device is available; using CPU", resolved)

    def parameters(
        self,
        profile: Any,
        resolved_device: str,
        *,
        feature_count: int,
        seed: int,
        num_threads: int,
    ) -> dict[str, Any]:
        del num_threads
        kwargs = dict(profile.model_kwargs)
        pt_kwargs = dict(kwargs.pop("pt_model_kwargs", {}))
        configured_dim = pt_kwargs.pop("input_dim", None)
        if configured_dim is not None and int(configured_dim) != feature_count:
            raise ValueError(
                f"DNN input_dim={configured_dim} does not match dataset feature count {feature_count}"
            )
        pt_kwargs["input_dim"] = feature_count
        kwargs["pt_model_kwargs"] = pt_kwargs
        kwargs["GPU"] = resolved_device
        kwargs.setdefault("seed", seed)
        return kwargs

    def build(self, parameters: Mapping[str, Any]) -> Any:
        from qlib.contrib.model.pytorch_nn import DNNModelPytorch

        return DNNModelPytorch(**dict(parameters))

    def save(self, model: Any, root: Path) -> str:
        import torch

        if not bool(getattr(model, "fitted", False)):
            raise ValueError("DNN model is not fitted")
        name = "model_state.pt"
        state = {key: value.detach().cpu() for key, value in model.dnn_model.state_dict().items()}
        torch.save(state, root / name)
        return name

    def scores(self, model: Any, features: pd.DataFrame) -> np.ndarray:
        import torch

        model.dnn_model.eval()
        values = torch.from_numpy(features.to_numpy(dtype=np.float32)).to(model.device)
        with torch.no_grad():
            result = model.dnn_model(values).detach().cpu().numpy()
        return np.asarray(result, dtype=float).reshape(-1)

    def load(
        self,
        root: Path,
        manifest: Mapping[str, Any],
        parameters: Mapping[str, Any],
        *,
        device: str,
    ) -> Any:
        import torch
        from qlib.contrib.model.pytorch_nn import Net

        kwargs = parameters.get("pt_model_kwargs", {})
        model = Net(**kwargs)
        state = torch.load(root / str(manifest["modelFile"]), map_location=device, weights_only=True)
        model.load_state_dict(state)
        model.to(torch.device(device))
        return model

    def predict_loaded(self, model: Any, features: pd.DataFrame) -> np.ndarray:
        import torch

        model.eval()
        tensor = torch.from_numpy(features.to_numpy(dtype=np.float32)).to(next(model.parameters()).device)
        with torch.no_grad():
            return np.asarray(model(tensor).detach().cpu().numpy(), dtype=float).reshape(-1)

    def selected_training_steps(self, model: Any, parameters: Mapping[str, Any]) -> int:
        selected = int(getattr(model, "best_step", 0) or 0)
        return selected or int(getattr(model, "max_steps", parameters.get("max_steps", 1)))

    def fit_final(self, model: Any, dataset: Any, selected_steps: int) -> None:
        dataset.segments = {key: value for key, value in dataset.segments.items() if key != "valid"}
        model.max_steps = selected_steps
        model.fit(dataset)
