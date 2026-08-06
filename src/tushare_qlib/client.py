from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import pandas as pd
import tushare as ts
from loguru import logger


@dataclass
class RetryPolicy:
    max_attempts: int = 6
    base_sleep_seconds: float = 2.0


class MinuteRateLimiter:
    def __init__(self, calls_per_minute: int):
        if calls_per_minute <= 0:
            raise ValueError("calls_per_minute must be positive")
        self.limit = calls_per_minute
        self.timestamps: deque[float] = deque()

    def acquire(self) -> None:
        while True:
            now = time.monotonic()
            while self.timestamps and now - self.timestamps[0] >= 60:
                self.timestamps.popleft()
            if len(self.timestamps) < self.limit:
                self.timestamps.append(now)
                return
            sleep_for = max(0.05, 60 - (now - self.timestamps[0]))
            time.sleep(sleep_for)


class TushareClient:
    """A small wrapper with configurable traffic control, retries and optional endpoints."""

    def __init__(
        self,
        token: str,
        calls_per_minute: int = 180,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        if not token:
            raise ValueError("token is empty")
        self.pro = ts.pro_api(token)
        self.limiter = MinuteRateLimiter(calls_per_minute)
        self.retry = retry_policy or RetryPolicy()

    @staticmethod
    def _is_permission_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return "权限" in str(exc) or "permission" in text or "2002" in text

    def call(
        self,
        api_name: str,
        *,
        fields: str | None = None,
        required: bool = True,
        **params: Any,
    ) -> pd.DataFrame:
        for attempt in range(1, self.retry.max_attempts + 1):
            self.limiter.acquire()
            try:
                api = getattr(self.pro, api_name)
                kwargs = dict(params)
                if fields:
                    kwargs["fields"] = fields
                result = api(**kwargs)
                if result is None:
                    return pd.DataFrame()
                if not isinstance(result, pd.DataFrame):
                    raise TypeError(f"{api_name} returned {type(result)!r}, expected DataFrame")
                return result
            except Exception as exc:  # SDK uses several exception types across versions.
                if self._is_permission_error(exc):
                    if required:
                        raise RuntimeError(f"No permission for required endpoint {api_name}: {exc}") from exc
                    logger.warning("Optional endpoint {} is unavailable: {}", api_name, exc)
                    return pd.DataFrame()
                if attempt >= self.retry.max_attempts:
                    if required:
                        raise RuntimeError(f"{api_name} failed after {attempt} attempts: {exc}") from exc
                    logger.warning("Optional endpoint {} failed after retries: {}", api_name, exc)
                    return pd.DataFrame()
                delay = self.retry.base_sleep_seconds * (2 ** (attempt - 1))
                logger.warning("{} attempt {}/{} failed; retry in {:.1f}s: {}", api_name, attempt,
                               self.retry.max_attempts, delay, exc)
                time.sleep(delay)
        raise AssertionError("unreachable")
