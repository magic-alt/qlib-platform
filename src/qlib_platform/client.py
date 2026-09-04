"""Deprecated compatibility surface for the former root-level Tushare client."""

from .data.sources.base import DataSourceClient, FetchResult, RetryPolicy
from .data.sources.tushare import MinuteRateLimiter, TushareClient

__all__ = [
    "DataSourceClient",
    "FetchResult",
    "MinuteRateLimiter",
    "RetryPolicy",
    "TushareClient",
]
