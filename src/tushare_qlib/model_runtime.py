from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

import yaml

from .models.registry import get_model_adapter
from .settings import Settings

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
        self._diagnostics: dict[str, float] = {}
        self._resource_deltas: dict[str, dict[str, int]] = {}

    @staticmethod
    def _process_resources() -> dict[str, int]:
        try:
            import psutil

            process = psutil.Process()
            snapshot = {
                "threads": int(process.num_threads()),
                "children": len(process.children(recursive=True)),
            }
            num_handles = getattr(process, "num_handles", None)
            if callable(num_handles):
                snapshot["handles"] = int(num_handles())
            return snapshot
        except (ImportError, OSError):
            return {}

    @contextmanager
    def measure(self, stage: str) -> Iterator[None]:
        started = self._clock()
        before = self._process_resources()
        try:
            yield
        finally:
            elapsed = max(0.0, self._clock() - started)
            self._values[stage] = self._values.get(stage, 0.0) + elapsed
            after = self._process_resources()
            if before and after:
                keys = before.keys() & after.keys()
                delta = {key: after[key] - before[key] for key in keys}
                previous = self._resource_deltas.get(stage, {})
                self._resource_deltas[stage] = {
                    key: previous.get(key, 0) + value for key, value in delta.items()
                }

    @contextmanager
    def measure_diagnostic(self, stage: str) -> Iterator[None]:
        """Record a nested sub-stage without double-counting totalSeconds."""

        started = self._clock()
        try:
            yield
        finally:
            elapsed = max(0.0, self._clock() - started)
            self._diagnostics[stage] = self._diagnostics.get(stage, 0.0) + elapsed

    def to_dict(self) -> dict[str, Any]:
        phases = {key: round(value, 6) for key, value in self._values.items()}
        diagnostics = {key: round(value, 6) for key, value in self._diagnostics.items()}
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
            "diagnosticPhasesSeconds": diagnostics,
            "totalSeconds": round(sum(self._values.values()), 6),
            "wallSeconds": round(max(0.0, self._clock() - self._wall_started), 6),
            "peakRssMb": peak_rss_mb,
            "reportRenderingIncluded": "report_seconds" in phases,
            "processSnapshot": self._process_resources(),
            "resourceDeltas": self._resource_deltas,
        }


def _profile_path(settings: Settings, override: str | Path | None) -> Path | None:
    if override is not None:
        return Path(override).expanduser().resolve()
    research = settings.data.get("research", {})
    legacy = research.get("model_profile") if isinstance(research, Mapping) else None
    experiment = settings.data.get("experiment", {})
    model = experiment.get("model", {}) if isinstance(experiment, Mapping) else {}
    configured = model.get("profile") if isinstance(model, Mapping) else None
    if configured and legacy and str(configured) != str(legacy):
        raise ValueError("experiment.model.profile conflicts with research.model_profile")
    configured = configured or legacy
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
    adapter = get_model_adapter(family)
    device_index = int(data.get("device_index", 0))
    if device_index < 0:
        raise ValueError("device_index must be non-negative")
    gpu_platform_id = int(data.get("gpu_platform_id", 0))
    if gpu_platform_id < 0:
        raise ValueError("gpu_platform_id must be non-negative")
    kwargs = data.get("model_kwargs", {})
    if not isinstance(kwargs, Mapping):
        raise ValueError("model_kwargs must be a mapping")
    profile = ModelProfile(name, family, device, device_index, dict(kwargs), source, gpu_platform_id)
    adapter.validate_profile(profile)
    return profile


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


def resolve_runtime(profile: ModelProfile) -> ResolvedRuntime:
    try:
        import qlib

        qlib_version = str(qlib.__version__)
    except (ImportError, AttributeError):
        qlib_version = "unavailable"
    resolution = get_model_adapter(profile.family).resolve_runtime(profile, {"qlib": qlib_version})
    return ResolvedRuntime(
        profile,
        resolution.resolved_device,
        resolution.fallback_reason,
        resolution.versions,
    )


def resolved_model_parameters(
    runtime: ResolvedRuntime, *, feature_count: int, seed: int, num_threads: int
) -> dict[str, Any]:
    return get_model_adapter(runtime.profile.family).parameters(
        runtime.profile,
        runtime.resolved_device,
        feature_count=feature_count,
        seed=seed,
        num_threads=num_threads,
    )


def build_model(runtime: ResolvedRuntime, *, feature_count: int, seed: int, num_threads: int) -> Any:
    kwargs = resolved_model_parameters(
        runtime, feature_count=feature_count, seed=seed, num_threads=num_threads
    )
    return get_model_adapter(runtime.profile.family).build(kwargs)


def write_timings(path: Path, runtime: ResolvedRuntime, timings: Mapping[str, Any]) -> None:
    payload = {"runtime": runtime.to_manifest(), "timings": dict(timings)}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
