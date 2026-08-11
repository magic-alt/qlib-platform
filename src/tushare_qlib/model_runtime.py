from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

import numpy as np
import yaml

from .settings import Settings


_DEFAULT_LIGHTGBM_KWARGS: dict[str, Any] = {
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

_BUILTIN_PROFILE: dict[str, Any] = {
    "name": "lightgbm_auto",
    "family": "lightgbm",
    "device": "auto",
    "device_index": 0,
    "model_kwargs": {},
}


@dataclass(frozen=True)
class ModelProfile:
    name: str
    family: str
    device: str
    device_index: int
    model_kwargs: dict[str, Any]
    source: str
    gpu_platform_id: int = 0

    @property
    def fingerprint(self) -> str:
        payload = {
            "name": self.name,
            "family": self.family,
            "device": self.device,
            "device_index": self.device_index,
            "gpu_platform_id": self.gpu_platform_id,
            "model_kwargs": self.model_kwargs,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        return hashlib.sha256(encoded).hexdigest()[:16]


@dataclass(frozen=True)
class ResolvedRuntime:
    profile: ModelProfile
    resolved_device: str
    fallback_reason: str | None
    versions: dict[str, str]

    @property
    def fingerprint(self) -> str:
        payload = f"{self.profile.fingerprint}|{self.resolved_device}".encode()
        return hashlib.sha256(payload).hexdigest()[:16]

    def to_manifest(self) -> dict[str, Any]:
        accelerated = self.resolved_device.startswith(("cuda", "gpu"))
        device_index = self.profile.device_index if accelerated else None
        return {
            "modelProfile": self.profile.name,
            "profileSource": self.profile.source,
            "profileFingerprint": self.profile.fingerprint,
            "runtimeFingerprint": self.fingerprint,
            "modelFamily": self.profile.family,
            "requestedDevice": self.profile.device,
            "resolvedDevice": self.resolved_device,
            "deviceIndex": device_index,
            "gpuPlatformId": self.profile.gpu_platform_id if accelerated else None,
            "fallbackReason": self.fallback_reason,
            "mpsFallbackEnabled": os.getenv("PYTORCH_ENABLE_MPS_FALLBACK") == "1",
            "versions": self.versions,
        }


class StageTimings:
    def __init__(self, clock: Callable[[], float] = time.perf_counter):
        self._clock = clock
        self._wall_started = clock()
        self._values: dict[str, float] = {}

    @contextmanager
    def measure(self, stage: str) -> Iterator[None]:
        started = self._clock()
        try:
            yield
        finally:
            elapsed = max(0.0, self._clock() - started)
            self._values[stage] = self._values.get(stage, 0.0) + elapsed

    def to_dict(self) -> dict[str, Any]:
        phases = {key: round(value, 6) for key, value in self._values.items()}
        peak_rss_mb: float | None = None
        try:
            import psutil

            memory = psutil.Process().memory_info()
            peak_bytes = getattr(memory, "peak_wset", memory.rss)
            peak_rss_mb = round(float(peak_bytes) / (1024.0 * 1024.0), 3)
        except (ImportError, OSError):
            pass
        return {
            "clock": "time.perf_counter",
            "phasesSeconds": phases,
            "totalSeconds": round(sum(self._values.values()), 6),
            "wallSeconds": round(max(0.0, self._clock() - self._wall_started), 6),
            "peakRssMb": peak_rss_mb,
            "reportRenderingIncluded": "report_seconds" in phases,
        }


def _profile_path(settings: Settings, override: str | Path | None) -> Path | None:
    if override is not None:
        return Path(override).expanduser().resolve()
    research = settings.data.get("research", {})
    configured = research.get("model_profile") if isinstance(research, Mapping) else None
    if not configured:
        return None
    path = Path(str(configured)).expanduser()
    return path.resolve() if path.is_absolute() else (settings.config_path.parent / path).resolve()


def _validate_profile(data: Mapping[str, Any], source: str) -> ModelProfile:
    allowed = {"name", "family", "device", "device_index", "gpu_platform_id", "model_kwargs"}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"model profile has unknown keys: {sorted(unknown)}")
    name = str(data.get("name", "")).strip()
    family = str(data.get("family", "")).strip().lower()
    device = str(data.get("device", "auto")).strip().lower()
    if not name:
        raise ValueError("model profile name is required")
    if family not in {"lightgbm", "pytorch_dnn"}:
        raise ValueError("model profile family must be lightgbm or pytorch_dnn")
    allowed_devices = (
        {"auto", "cpu", "cuda", "gpu"} if family == "lightgbm" else {"auto", "cpu", "cuda", "mps"}
    )
    if device not in allowed_devices:
        raise ValueError(f"device {device!r} is not supported by {family}")
    device_index = int(data.get("device_index", 0))
    if device_index < 0:
        raise ValueError("device_index must be non-negative")
    gpu_platform_id = int(data.get("gpu_platform_id", 0))
    if gpu_platform_id < 0:
        raise ValueError("gpu_platform_id must be non-negative")
    kwargs = data.get("model_kwargs", {})
    if not isinstance(kwargs, Mapping):
        raise ValueError("model_kwargs must be a mapping")
    return ModelProfile(name, family, device, device_index, dict(kwargs), source, gpu_platform_id)


def load_model_profile(settings: Settings, override: str | Path | None = None) -> ModelProfile:
    path = _profile_path(settings, override)
    if path is None:
        return _validate_profile(_BUILTIN_PROFILE, "builtin:lightgbm_auto")
    if not path.is_file():
        raise FileNotFoundError(f"model profile not found: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, Mapping):
        raise ValueError("model profile root must be a mapping")
    return _validate_profile(loaded, str(path))


def _probe_lightgbm_cuda(device_index: int) -> tuple[bool, str | None, str]:
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
    except Exception as exc:  # LightGBM exposes backend failures through several exception types.
        return False, f"LightGBM CUDA probe failed: {exc}", str(lgb.__version__)
    return True, None, str(lgb.__version__)


def _probe_lightgbm_opencl(platform_id: int, device_index: int) -> tuple[bool, str | None, str]:
    """Probe LightGBM's OpenCL ``gpu`` backend with a real one-tree fit."""

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


def resolve_runtime(profile: ModelProfile) -> ResolvedRuntime:
    try:
        import qlib

        qlib_version = str(qlib.__version__)
    except (ImportError, AttributeError):
        qlib_version = "unavailable"
    versions = {"qlib": qlib_version}
    if profile.family == "lightgbm":
        if profile.device == "cpu":
            import lightgbm as lgb

            versions["lightgbm"] = str(lgb.__version__)
            return ResolvedRuntime(profile, "cpu", None, versions)
        if profile.device == "gpu":
            available, reason, version = _probe_lightgbm_opencl(profile.gpu_platform_id, profile.device_index)
            versions["lightgbm"] = version
            if not available:
                raise RuntimeError(reason or "LightGBM OpenCL GPU is unavailable")
            return ResolvedRuntime(profile, f"gpu:{profile.device_index}", None, versions)
        if profile.device == "auto" and sys.platform.startswith("win"):
            available, reason, version = _probe_lightgbm_opencl(profile.gpu_platform_id, profile.device_index)
            versions["lightgbm"] = version
            return ResolvedRuntime(
                profile,
                f"gpu:{profile.device_index}" if available else "cpu",
                None if available else reason,
                versions,
            )
        available, reason, version = _probe_lightgbm_cuda(profile.device_index)
        versions["lightgbm"] = version
        if profile.device == "cuda":
            if not available:
                raise RuntimeError(reason or "LightGBM CUDA is unavailable")
            return ResolvedRuntime(profile, f"cuda:{profile.device_index}", None, versions)
        return ResolvedRuntime(
            profile,
            f"cuda:{profile.device_index}" if available else "cpu",
            None if available else reason,
            versions,
        )

    try:
        import torch
    except ImportError as exc:
        raise RuntimeError(
            "PyTorch profile selected but torch is not installed; install the pytorch extra"
        ) from exc
    versions["torch"] = str(torch.__version__)
    cuda_available = bool(torch.cuda.is_available())
    mps_backend = getattr(torch.backends, "mps", None)
    mps_available = bool(mps_backend and mps_backend.is_built() and mps_backend.is_available())
    if profile.device == "cpu":
        return ResolvedRuntime(profile, "cpu", None, versions)
    if profile.device == "cuda":
        if not cuda_available:
            raise RuntimeError("PyTorch CUDA was explicitly requested but torch.cuda.is_available() is false")
        return ResolvedRuntime(profile, f"cuda:{profile.device_index}", None, versions)
    if profile.device == "mps":
        if not mps_available:
            raise RuntimeError("PyTorch MPS was explicitly requested but the MPS backend is not available")
        return ResolvedRuntime(profile, "mps", None, versions)
    if cuda_available:
        return ResolvedRuntime(profile, f"cuda:{profile.device_index}", None, versions)
    if mps_available:
        return ResolvedRuntime(profile, "mps", None, versions)
    return ResolvedRuntime(profile, "cpu", "No PyTorch CUDA or MPS device is available; using CPU", versions)


def resolved_model_parameters(
    runtime: ResolvedRuntime, *, feature_count: int, seed: int, num_threads: int
) -> dict[str, Any]:
    kwargs = dict(runtime.profile.model_kwargs)
    if runtime.profile.family == "lightgbm":
        params = {**_DEFAULT_LIGHTGBM_KWARGS, "num_threads": num_threads, **kwargs}
        params.update(
            {
                "seed": seed,
                "feature_fraction_seed": seed,
                "bagging_seed": seed,
                "data_random_seed": seed,
                "device_type": (
                    "cuda"
                    if runtime.resolved_device.startswith("cuda")
                    else "gpu"
                    if runtime.resolved_device.startswith("gpu")
                    else "cpu"
                ),
            }
        )
        if runtime.resolved_device.startswith("cuda"):
            params["gpu_device_id"] = runtime.profile.device_index
        elif runtime.resolved_device.startswith("gpu"):
            params["gpu_platform_id"] = runtime.profile.gpu_platform_id
            params["gpu_device_id"] = runtime.profile.device_index
            params.setdefault("gpu_use_dp", False)
        else:
            params.pop("gpu_device_id", None)
            params.pop("gpu_platform_id", None)
            params.pop("gpu_use_dp", None)
        return params

    pt_kwargs = dict(kwargs.pop("pt_model_kwargs", {}))
    configured_dim = pt_kwargs.pop("input_dim", None)
    if configured_dim is not None and int(configured_dim) != feature_count:
        raise ValueError(
            f"DNN input_dim={configured_dim} does not match dataset feature count {feature_count}"
        )
    pt_kwargs["input_dim"] = feature_count
    kwargs["pt_model_kwargs"] = pt_kwargs
    kwargs["GPU"] = runtime.resolved_device
    kwargs.setdefault("seed", seed)
    return kwargs


def build_model(runtime: ResolvedRuntime, *, feature_count: int, seed: int, num_threads: int) -> Any:
    kwargs = resolved_model_parameters(
        runtime, feature_count=feature_count, seed=seed, num_threads=num_threads
    )
    if runtime.profile.family == "lightgbm":
        from qlib.contrib.model.gbdt import LGBModel

        return LGBModel(**kwargs)
    from qlib.contrib.model.pytorch_nn import DNNModelPytorch

    return DNNModelPytorch(**kwargs)


def write_timings(path: Path, runtime: ResolvedRuntime, timings: Mapping[str, Any]) -> None:
    payload = {"runtime": runtime.to_manifest(), "timings": dict(timings)}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
