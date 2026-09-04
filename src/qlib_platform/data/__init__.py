"""Data ingestion, storage contracts, and provider adapters."""

from .ingestion import Extractor
from .sources import (
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
