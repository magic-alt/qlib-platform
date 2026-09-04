"""Compatibility helpers for source adapters that historically imported ``.client``."""

from qlib_platform.data.sources.base import DataSourceClient, FetchResult, RetryPolicy

__all__ = ["DataSourceClient", "FetchResult", "RetryPolicy"]
