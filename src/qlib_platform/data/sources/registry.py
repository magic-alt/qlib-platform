from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from .base import DataSourceClient, RetryPolicy

if TYPE_CHECKING:
    from ...settings import Settings


@dataclass(frozen=True)
class EndpointOverride:
    required: bool | None = None
    enabled: bool | None = None


@dataclass(frozen=True)
class DataSourceBinding:
    """Resolved provider plus provider-specific ingestion capabilities."""

    name: str
    client: DataSourceClient
    endpoint_overrides: Mapping[str, EndpointOverride] = field(default_factory=dict)
    capabilities: frozenset[str] = field(default_factory=frozenset)


DataSourceFactory = Callable[["Settings", RetryPolicy], DataSourceBinding]
_FACTORIES: dict[str, DataSourceFactory] = {}
_ALIASES: dict[str, str] = {}
_BUILTINS_REGISTERED = False


def _normalize(name: str) -> str:
    return name.strip().lower().replace("-", "_")


def register_data_source(
    name: str,
    factory: DataSourceFactory,
    *,
    aliases: tuple[str, ...] = (),
    replace: bool = False,
) -> None:
    """Register a source adapter without changing the ingestion pipeline."""

    canonical = _normalize(name)
    if not canonical:
        raise ValueError("data source name must not be empty")
    if canonical in _FACTORIES and not replace:
        raise ValueError(f"data source already registered: {canonical}")
    _FACTORIES[canonical] = factory
    for alias in aliases:
        normalized = _normalize(alias)
        if normalized in _ALIASES and _ALIASES[normalized] != canonical and not replace:
            raise ValueError(f"data source alias already registered: {normalized}")
        _ALIASES[normalized] = canonical


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _optional_endpoints(settings: "Settings") -> Mapping[str, Any]:
    source_cfg = _mapping(settings.data.get("data_source"))
    neutral = source_cfg.get("optional_endpoints")
    if isinstance(neutral, Mapping):
        return neutral
    legacy = _mapping(settings.data.get("tushare"))
    value = legacy.get("optional_endpoints")
    return value if isinstance(value, Mapping) else {}


def _tushare_factory(settings: "Settings", retry_policy: RetryPolicy) -> DataSourceBinding:
    from .tushare import TushareClient

    source_cfg = _mapping(settings.data.get("data_source"))
    legacy = _mapping(settings.data.get("tushare"))
    provider_cfg = _mapping(source_cfg.get("tushare")) or legacy
    calls = int(os.getenv("TUSHARE_CALLS_PER_MINUTE", provider_cfg.get("calls_per_minute", 180)))
    client = TushareClient(
        settings.require_token(),
        calls_per_minute=calls,
        retry_policy=retry_policy,
    )
    return DataSourceBinding(name="tushare", client=client)


def _mysql_factory(settings: "Settings", retry_policy: RetryPolicy) -> DataSourceBinding:
    from .mysql import MysqlClient, build_connection_kwargs, build_mysql_endpoints

    source_cfg = _mapping(settings.data.get("data_source"))
    mysql_cfg = source_cfg.get("mysql")
    if not isinstance(mysql_cfg, Mapping):
        raise ValueError("data_source.kind=mysql requires data_source.mysql configuration")
    endpoint_cfg = build_mysql_endpoints(mysql_cfg, optional_endpoints=_optional_endpoints(settings))
    client = MysqlClient(
        connection=build_connection_kwargs(mysql_cfg),
        endpoint_queries={name: value["query"] for name, value in endpoint_cfg.items()},
        default_params={
            "source": str(mysql_cfg.get("source", "tushare")).strip(),
            "universe": str(mysql_cfg.get("universe", "CSI300")).strip(),
        },
        retry_policy=retry_policy,
    )
    overrides = {
        name: EndpointOverride(
            required=bool(value["required"]),
            enabled=bool(value.get("enabled", True)),
        )
        for name, value in endpoint_cfg.items()
    }
    return DataSourceBinding(
        name="mysql",
        client=client,
        endpoint_overrides=overrides,
        capabilities=frozenset({"mysql"}),
    )


def _register_builtins() -> None:
    global _BUILTINS_REGISTERED
    if _BUILTINS_REGISTERED:
        return
    register_data_source("tushare", _tushare_factory)
    register_data_source(
        "mysql",
        _mysql_factory,
        aliases=("lean_mysql", "lean-platform", "lean_platform"),
    )
    _BUILTINS_REGISTERED = True


def available_data_sources() -> tuple[str, ...]:
    _register_builtins()
    return tuple(sorted(_FACTORIES))


def resolve_data_source_name(settings: "Settings") -> str:
    _register_builtins()
    source_cfg = _mapping(settings.data.get("data_source"))
    requested = _normalize(str(source_cfg.get("kind", "tushare")))
    if requested == "auto":
        requested = "mysql" if isinstance(source_cfg.get("mysql"), Mapping) else "tushare"
    return _ALIASES.get(requested, requested)


def create_data_source(settings: "Settings", retry_policy: RetryPolicy) -> DataSourceBinding:
    """Resolve and construct the configured provider.

    Adding a provider is an adapter-registration concern; callers do not need a
    new ``if provider == ...`` branch.
    """

    name = resolve_data_source_name(settings)
    factory = _FACTORIES.get(name)
    if factory is None:
        available = ", ".join(available_data_sources())
        raise ValueError(f"unsupported data_source.kind={settings.source_kind!r}; registered: {available}")
    return factory(settings, retry_policy)
