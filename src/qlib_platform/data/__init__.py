"""Data ingestion, storage contracts, and provider adapters."""

from qlib_platform.data.ingestion import Extractor
from qlib_platform.data.sources import (
    DataSourceBinding,
    DataSourceClient,
    FetchResult,
    RetryPolicy,
    available_data_sources,
    create_data_source,
    register_data_source,
)

__all__ = [
    "DataSourceBinding",
    "DataSourceClient",
    "Extractor",
    "FetchResult",
    "RetryPolicy",
    "available_data_sources",
    "create_data_source",
    "register_data_source",
]
