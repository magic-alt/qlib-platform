"""Qlib workflow-record adapters for YAML-only workflows."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from qlib.workflow.record_temp import PortAnaRecord


class ASharePortAnaRecord(PortAnaRecord):
    """Convert YAML sequences to the tuple arguments expected by Qlib's exchange."""

    def __init__(self, recorder: Any, config: dict[str, Any] | None = None, **kwargs: Any) -> None:
        normalized_config = deepcopy(config) if config is not None else None
        if normalized_config is not None:
            exchange_kwargs = normalized_config.get("backtest", {}).get("exchange_kwargs", {})
            for key in ("limit_threshold", "volume_threshold"):
                value = exchange_kwargs.get(key)
                if isinstance(value, list):
                    exchange_kwargs[key] = tuple(value)
        super().__init__(recorder=recorder, config=normalized_config, **kwargs)
