from __future__ import annotations

from qlib_platform.models.base import ModelAdapter


_MODEL_REGISTRY: dict[str, ModelAdapter] = {}


def register_model_adapter(adapter: ModelAdapter) -> ModelAdapter:
    family = adapter.family.strip().lower()
    if not family:
        raise ValueError("model adapter family is required")
    if family in _MODEL_REGISTRY:
        raise ValueError(f"duplicate model adapter family: {family}")
    _MODEL_REGISTRY[family] = adapter
    return adapter


def get_model_adapter(family: str) -> ModelAdapter:
    normalized = family.strip().lower()
    try:
        return _MODEL_REGISTRY[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported model family {family!r}; registered={list(model_families())}") from exc


def model_families() -> tuple[str, ...]:
    return tuple(sorted(_MODEL_REGISTRY))


from qlib_platform.models.adapters.lightgbm import LightGBMAdapter  # noqa: E402
from qlib_platform.models.adapters.pytorch_dnn import PyTorchDNNAdapter  # noqa: E402
from qlib_platform.models.adapters.ridge import RidgeAdapter  # noqa: E402
from qlib_platform.models.adapters.xgboost import XGBoostAdapter  # noqa: E402


for _adapter in (RidgeAdapter(), LightGBMAdapter(), XGBoostAdapter(), PyTorchDNNAdapter()):
    register_model_adapter(_adapter)
