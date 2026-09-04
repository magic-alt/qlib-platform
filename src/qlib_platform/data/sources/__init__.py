"""Provider-neutral market-data adapters."""

from .base import DataSourceClient, FetchResult, RetryPolicy
from .registry import (
    DataSourceBinding,
    DataSourceFactory,
    EndpointOverride,
    available_data_sources,
    create_data_source,
    register_data_source,
    resolve_data_source_name,
)

__all__ = [
    "DataSourceBinding",
    "DataSourceClient",
    "DataSourceFactory",
    "EndpointOverride",
    "FetchResult",
    "RetryPolicy",
    "available_data_sources",
    "create_data_source",
    "register_data_source",
    "resolve_data_source_name",
]
