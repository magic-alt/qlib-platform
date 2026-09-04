from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import pandas as pd


@dataclass(frozen=True)
class RetryPolicy:
    """Provider-neutral retry policy used by market-data adapters."""

    max_attempts: int = 6
    base_sleep_seconds: float = 2.0
    max_sleep_seconds: float = 60.0
    jitter_ratio: float = 0.15


@dataclass(frozen=True)
class FetchResult:
    """Normalized result returned by every pull-style data source."""

    data: pd.DataFrame
    status: str
    attempts: int
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status in {"success", "empty"}


@runtime_checkable
class DataSourceClient(Protocol):
    """Small transport contract consumed by the ingestion pipeline.

    Providers may use HTTP APIs, databases, files, or other transports.  The
    research pipeline only depends on this normalized tabular contract.
    """

    def fetch(
        self,
        api_name: str,
        *,
        fields: str | None = None,
        required: bool = True,
        **params: Any,
    ) -> FetchResult: ...

    def call(
        self,
        api_name: str,
        *,
        fields: str | None = None,
        required: bool = True,
        **params: Any,
    ) -> pd.DataFrame: ...
