"""Provider-neutral market-data adapters."""

from qlib_platform.data.sources.base import DataSourceClient, FetchResult, RetryPolicy
from qlib_platform.data.sources.registry import (
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
