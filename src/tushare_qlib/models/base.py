from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class RuntimeResolution:
    resolved_device: str
    fallback_reason: str | None
    versions: dict[str, str]


class ModelAdapter(ABC):
    """One model family's complete research and deployment lifecycle."""

    family: str
    allowed_devices: frozenset[str]
    parity_tolerance: float = 1e-6

    def validate_profile(self, profile: Any) -> None:
        if profile.device not in self.allowed_devices:
            raise ValueError(f"device {profile.device!r} is not supported by {self.family}")

    @abstractmethod
    def resolve_runtime(self, profile: Any, versions: Mapping[str, str]) -> RuntimeResolution:
        raise NotImplementedError

    @abstractmethod
    def parameters(
        self,
        profile: Any,
        resolved_device: str,
        *,
        feature_count: int,
        seed: int,
        num_threads: int,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def build(self, parameters: Mapping[str, Any]) -> Any:
        raise NotImplementedError

    @abstractmethod
    def save(self, model: Any, root: Path) -> str:
        raise NotImplementedError

    @abstractmethod
    def scores(self, model: Any, features: pd.DataFrame) -> np.ndarray:
        raise NotImplementedError

    @abstractmethod
    def load(
        self,
        root: Path,
        manifest: Mapping[str, Any],
        parameters: Mapping[str, Any],
        *,
        device: str,
    ) -> Any:
        raise NotImplementedError

    @abstractmethod
    def predict_loaded(self, model: Any, features: pd.DataFrame) -> np.ndarray:
        raise NotImplementedError

    def selected_training_steps(self, model: Any, parameters: Mapping[str, Any]) -> int:
        return 1

    def fit_final(self, model: Any, dataset: Any, selected_steps: int) -> None:
        del selected_steps
        dataset.segments = {key: value for key, value in dataset.segments.items() if key != "valid"}
        model.fit(dataset)
