from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from ..base import ModelAdapter, RuntimeResolution


_DEFAULTS: dict[str, Any] = {
    "loss": "mse",
    "learning_rate": 0.03,
    "num_leaves": 63,
    "max_depth": 8,
    "colsample_bytree": 0.8,
    "subsample": 0.8,
    "lambda_l1": 10.0,
    "lambda_l2": 50.0,
    "max_bin": 63,
    "early_stopping_rounds": 100,
    "num_boost_round": 2000,
}


def _nvidia_device_name(device_index: int) -> str | None:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return None
    try:
        result = subprocess.run(
            [executable, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode:
        return None
    names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return names[device_index] if device_index < len(names) else None


def opencl_device_name(platform_id: int, device_index: int) -> str | None:
    """Best-effort device naming without making pyopencl a runtime requirement."""

    try:
        import pyopencl as cl

        platforms = cl.get_platforms()
        if platform_id < len(platforms):
            devices = platforms[platform_id].get_devices(device_type=cl.device_type.GPU)
            if device_index < len(devices):
                name = str(devices[device_index].name).strip()
                if name:
                    return name
    except (ImportError, OSError, RuntimeError):
        pass
    # NVIDIA's Windows driver installs nvidia-smi even when LightGBM uses the
    # OpenCL backend. This gives a useful hardware name without changing backend
    # selection or introducing a mandatory OpenCL Python dependency.
    return _nvidia_device_name(device_index)


def probe_cuda(device_index: int) -> tuple[bool, str | None, str]:
    import lightgbm as lgb

    if not sys.platform.startswith("linux"):
        return False, "LightGBM CUDA requires Linux; using CPU", str(lgb.__version__)
    features = np.asarray([[0.0], [1.0], [2.0], [3.0]], dtype=np.float32)
    labels = np.asarray([0.0, 1.0, 0.0, 1.0], dtype=np.float32)
    try:
        lgb.train(
            {
                "objective": "regression",
                "device_type": "cuda",
                "gpu_device_id": device_index,
                "max_bin": 63,
                "min_data_in_leaf": 1,
                "verbosity": -1,
            },
            lgb.Dataset(features, label=labels),
            num_boost_round=1,
        )
    except Exception as exc:
        return False, f"LightGBM CUDA probe failed: {exc}", str(lgb.__version__)
    return True, None, str(lgb.__version__)


def probe_opencl(platform_id: int, device_index: int) -> tuple[bool, str | None, str]:
    import lightgbm as lgb

    features = np.asarray([[0.0], [1.0], [2.0], [3.0]], dtype=np.float32)
    labels = np.asarray([0.0, 1.0, 0.0, 1.0], dtype=np.float32)
    try:
        lgb.train(
            {
                "objective": "regression",
                "device_type": "gpu",
                "gpu_platform_id": platform_id,
                "gpu_device_id": device_index,
                "gpu_use_dp": False,
                "max_bin": 63,
                "min_data_in_leaf": 1,
                "verbosity": -1,
            },
            lgb.Dataset(features, label=labels),
            num_boost_round=1,
        )
    except Exception as exc:
        return False, f"LightGBM OpenCL GPU probe failed: {exc}", str(lgb.__version__)
    return True, None, str(lgb.__version__)


class LightGBMAdapter(ModelAdapter):
    family = "lightgbm"
    allowed_devices = frozenset({"auto", "cpu", "cuda", "gpu"})

    def resolve_runtime(self, profile: Any, versions: Mapping[str, str]) -> RuntimeResolution:
        import lightgbm as lgb

        resolved = dict(versions)
        resolved["lightgbm"] = str(lgb.__version__)
        if profile.device == "cpu":
            return RuntimeResolution("cpu", None, resolved)
        if profile.device == "gpu":
            available, reason, _ = probe_opencl(profile.gpu_platform_id, profile.device_index)
            if not available:
                raise RuntimeError(reason or "LightGBM OpenCL GPU is unavailable")
            return RuntimeResolution(
                f"gpu:{profile.device_index}",
                None,
                resolved,
                opencl_device_name(profile.gpu_platform_id, profile.device_index),
            )
        if profile.device == "auto" and sys.platform.startswith("win"):
            available, reason, _ = probe_opencl(profile.gpu_platform_id, profile.device_index)
            return RuntimeResolution(
                f"gpu:{profile.device_index}" if available else "cpu",
                None if available else reason,
                resolved,
                opencl_device_name(profile.gpu_platform_id, profile.device_index) if available else None,
            )
        available, reason, _ = probe_cuda(profile.device_index)
        if profile.device == "cuda" and not available:
            raise RuntimeError(reason or "LightGBM CUDA is unavailable")
        return RuntimeResolution(
            f"cuda:{profile.device_index}" if available else "cpu",
            None if available else reason,
            resolved,
            _nvidia_device_name(profile.device_index) if available else None,
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
        params = {**_DEFAULTS, "num_threads": num_threads, **dict(profile.model_kwargs)}
        params.update(
            {
                "seed": seed,
                "feature_fraction_seed": seed,
                "bagging_seed": seed,
                "data_random_seed": seed,
                "device_type": (
                    "cuda"
                    if resolved_device.startswith("cuda")
                    else "gpu"
                    if resolved_device.startswith("gpu")
                    else "cpu"
                ),
            }
        )
        if resolved_device.startswith("cuda"):
            params["gpu_device_id"] = profile.device_index
        elif resolved_device.startswith("gpu"):
            params["gpu_platform_id"] = profile.gpu_platform_id
            params["gpu_device_id"] = profile.device_index
            params.setdefault("gpu_use_dp", False)
        else:
            params.pop("gpu_device_id", None)
            params.pop("gpu_platform_id", None)
            params.pop("gpu_use_dp", None)
        return params

    def build(self, parameters: Mapping[str, Any]) -> Any:
        from qlib.contrib.model.gbdt import LGBModel

        return LGBModel(**dict(parameters))

    def save(self, model: Any, root: Path) -> str:
        if getattr(model, "model", None) is None:
            raise ValueError("LightGBM model is not fitted")
        name = "model.txt"
        model.model.save_model(str(root / name))
        return name

    def scores(self, model: Any, features: pd.DataFrame) -> np.ndarray:
        return np.asarray(model.model.predict(features.to_numpy()), dtype=float).reshape(-1)

    def load(
        self,
        root: Path,
        manifest: Mapping[str, Any],
        parameters: Mapping[str, Any],
        *,
        device: str,
    ) -> Any:
        del parameters, device
        import lightgbm as lgb

        return lgb.Booster(model_file=str(root / str(manifest["modelFile"])))

    def predict_loaded(self, model: Any, features: pd.DataFrame) -> np.ndarray:
        return np.asarray(model.predict(features.to_numpy()), dtype=float).reshape(-1)

    def selected_training_steps(self, model: Any, parameters: Mapping[str, Any]) -> int:
        booster = getattr(model, "model", None)
        selected = int(getattr(booster, "best_iteration", 0) or 0)
        return selected or int(getattr(model, "num_boost_round", parameters.get("num_boost_round", 1)))

    def fit_final(self, model: Any, dataset: Any, selected_steps: int) -> None:
        dataset.segments = {key: value for key, value in dataset.segments.items() if key != "valid"}
        model.fit(dataset, num_boost_round=selected_steps, early_stopping_rounds=0)
