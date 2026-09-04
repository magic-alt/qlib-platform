"""Compatibility helpers for source adapters that historically imported ``.client``."""

from .base import DataSourceClient, FetchResult, RetryPolicy

__all__ = ["DataSourceClient", "FetchResult", "RetryPolicy"]
