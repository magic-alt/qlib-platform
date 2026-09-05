from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from qlib_platform.qlib_compat.spec import QlibConfig, QlibObjectSpec, as_qlib_config


def init_qlib_object(
    config: QlibConfig | QlibObjectSpec,
    *,
    default_module: object | None = None,
    accept_types: type | tuple[type, ...] = (),
    try_kwargs: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    """Delegate object construction to Qlib without a platform class allowlist."""

    from qlib.utils import init_instance_by_config

    return init_instance_by_config(
        as_qlib_config(config),
        default_module=default_module,
        accept_types=accept_types,
        try_kwargs=dict(try_kwargs or {}),
        **kwargs,
    )


def build_model(config: QlibConfig | QlibObjectSpec, **kwargs: Any) -> Any:
    from qlib.model.base import Model

    return init_qlib_object(config, accept_types=Model, **kwargs)


def build_dataset(config: QlibConfig | QlibObjectSpec, **kwargs: Any) -> Any:
    from qlib.data.dataset import Dataset

    return init_qlib_object(config, accept_types=Dataset, **kwargs)


def build_handler(config: QlibConfig | QlibObjectSpec, **kwargs: Any) -> Any:
    from qlib.data.dataset.handler import DataHandler

    return init_qlib_object(config, accept_types=DataHandler, **kwargs)


def build_processor(config: QlibConfig | QlibObjectSpec, **kwargs: Any) -> Any:
    from qlib.data.dataset.processor import Processor

    return init_qlib_object(config, accept_types=Processor, **kwargs)


def build_strategy(config: QlibConfig | QlibObjectSpec, **kwargs: Any) -> Any:
    from qlib.strategy.base import BaseStrategy

    return init_qlib_object(config, accept_types=BaseStrategy, **kwargs)


def build_executor(config: QlibConfig | QlibObjectSpec, **kwargs: Any) -> Any:
    from qlib.backtest.executor import BaseExecutor

    return init_qlib_object(config, accept_types=BaseExecutor, **kwargs)
