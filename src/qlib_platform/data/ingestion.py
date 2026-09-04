from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .._extract_legacy import (
    ADJ_FIELDS,
    BASIC_FIELDS,
    DAILY_FIELDS,
    LIMIT_FIELDS,
    MONEYFLOW_FIELDS,
    ST_FIELDS,
    SUSPEND_FIELDS,
    Endpoint,
    Extractor as _LegacyExtractor,
)
from ..store import PartitionStore
from .sources import RetryPolicy, create_data_source


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _source_runtime_config(settings: Any) -> Mapping[str, Any]:
    source_cfg = _mapping(settings.data.get("data_source"))
    runtime = source_cfg.get("runtime")
    if isinstance(runtime, Mapping):
        return runtime
    # Backward compatibility for existing pipeline YAMLs.  New configs should
    # move retry/endpoint knobs under ``data_source`` rather than a vendor block.
    return _mapping(settings.data.get("tushare"))


def _optional_endpoints(settings: Any) -> Mapping[str, Any]:
    source_cfg = _mapping(settings.data.get("data_source"))
    value = source_cfg.get("optional_endpoints")
    if isinstance(value, Mapping):
        return value
    legacy = _mapping(settings.data.get("tushare"))
    value = legacy.get("optional_endpoints")
    return value if isinstance(value, Mapping) else {}


class Extractor(_LegacyExtractor):
    """Provider-neutral ingestion orchestrator.

    The extraction behavior remains compatible with the certified pipeline, but
    source construction is delegated to the adapter registry.  New providers
    implement the normalized client contract and register a factory; this class
    does not gain another provider-specific constructor branch.
    """

    def __init__(self, settings: Any):
        runtime = _source_runtime_config(settings)
        retry_policy = RetryPolicy(
            int(runtime.get("max_attempts", 6)),
            float(runtime.get("base_sleep_seconds", 2.0)),
            float(runtime.get("max_sleep_seconds", 60.0)),
            float(runtime.get("jitter_ratio", 0.15)),
        )
        binding = create_data_source(settings, retry_policy)

        self.settings = settings
        self.store = PartitionStore(settings.paths.raw)
        self.data_source = binding
        self.client = binding.client
        # Only retained for inherited Lean/MySQL optimized range paths.  Generic
        # provider selection itself is handled by the registry above.
        self.source_is_mysql = "mysql" in binding.capabilities

        optional = _optional_endpoints(settings)
        endpoints = [
            Endpoint("daily", DAILY_FIELDS, True, enabled=bool(optional.get("daily", True))),
            Endpoint("adj_factor", ADJ_FIELDS, True, enabled=bool(optional.get("adj_factor", True))),
            Endpoint("daily_basic", BASIC_FIELDS, True, enabled=bool(optional.get("daily_basic", True))),
            Endpoint("moneyflow", MONEYFLOW_FIELDS, False, enabled=bool(optional.get("moneyflow", True))),
            Endpoint("stk_limit", LIMIT_FIELDS, False, enabled=bool(optional.get("stk_limit", True))),
            Endpoint("suspend_d", SUSPEND_FIELDS, False, enabled=bool(optional.get("suspend_d", True))),
            Endpoint("stock_st", ST_FIELDS, False, enabled=bool(optional.get("stock_st", True))),
        ]
        self.endpoints = []
        for endpoint in endpoints:
            override = binding.endpoint_overrides.get(endpoint.name)
            if override is None:
                self.endpoints.append(endpoint)
                continue
            self.endpoints.append(
                Endpoint(
                    endpoint.name,
                    endpoint.fields,
                    endpoint.required if override.required is None else override.required,
                    endpoint.enabled if override.enabled is None else override.enabled,
                )
            )
