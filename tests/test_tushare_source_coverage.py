from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from qlib_platform.data.sources.base import RetryPolicy
from qlib_platform.data.sources import tushare as tushare_source


class _Pro:
    def __init__(self, results: list[object]):
        self.results = list(results)
        self.calls: list[dict[str, object]] = []

    def daily(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        value = self.results.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def _client(monkeypatch, results: list[object], *, retry: RetryPolicy | None = None):
    pro = _Pro(results)
    monkeypatch.setattr(tushare_source, "ts", SimpleNamespace(pro_api=lambda token: pro))
    client = tushare_source.TushareClient(
        "token",
        calls_per_minute=100,
        retry_policy=retry
        or RetryPolicy(max_attempts=2, base_sleep_seconds=0.01, max_sleep_seconds=0.02, jitter_ratio=0),
    )
    monkeypatch.setattr(client.limiter, "acquire", lambda: None)
    return client, pro


def test_minute_rate_limiter_validation_and_capacity(monkeypatch) -> None:
    with pytest.raises(ValueError, match="positive"):
        tushare_source.MinuteRateLimiter(0)
    limiter = tushare_source.MinuteRateLimiter(2)
    times = iter([0.0, 0.0, 0.0, 60.1])
    monkeypatch.setattr(tushare_source.time, "monotonic", lambda: next(times))
    sleeps: list[float] = []
    monkeypatch.setattr(tushare_source.time, "sleep", lambda value: sleeps.append(value))
    limiter.acquire()
    limiter.acquire()
    limiter.acquire()
    assert len(limiter.timestamps) == 1
    assert sleeps and sleeps[0] >= 0.05


def test_tushare_client_requires_token_and_dependency(monkeypatch) -> None:
    monkeypatch.setattr(tushare_source, "ts", SimpleNamespace(pro_api=lambda token: object()))
    with pytest.raises(ValueError, match="token is empty"):
        tushare_source.TushareClient(None)
    monkeypatch.setattr(tushare_source, "ts", None)
    with pytest.raises(RuntimeError, match="tushare is not installed"):
        tushare_source.TushareClient("token")


def test_permission_error_detection() -> None:
    assert tushare_source.TushareClient._is_permission_error(RuntimeError("permission denied"))
    assert tushare_source.TushareClient._is_permission_error(RuntimeError("2002 error"))
    assert tushare_source.TushareClient._is_permission_error(RuntimeError("无权限"))
    assert not tushare_source.TushareClient._is_permission_error(RuntimeError("timeout"))


def test_fetch_success_empty_none_and_call(monkeypatch) -> None:
    client, pro = _client(monkeypatch, [pd.DataFrame({"x": [1]}), None, pd.DataFrame({"x": [2]})])
    result = client.fetch("daily", fields="x", trade_date="20260901")
    assert result.status == "success"
    assert result.attempts == 1
    assert pro.calls[0]["fields"] == "x"
    empty = client.fetch("daily")
    assert empty.status == "empty"
    assert empty.data.empty
    frame = client.call("daily")
    assert frame["x"].tolist() == [2]


def test_fetch_rejects_non_dataframe_and_retries_required(monkeypatch) -> None:
    retry = RetryPolicy(max_attempts=2, base_sleep_seconds=0.1, max_sleep_seconds=0.1, jitter_ratio=0)
    client, _ = _client(monkeypatch, ["bad", pd.DataFrame({"x": [1]})], retry=retry)
    sleeps: list[float] = []
    monkeypatch.setattr(tushare_source.time, "sleep", lambda value: sleeps.append(value))
    result = client.fetch("daily")
    assert result.status == "success"
    assert result.attempts == 2
    assert sleeps == [pytest.approx(0.1)]

    client, _ = _client(monkeypatch, [RuntimeError("timeout"), RuntimeError("still down")], retry=retry)
    monkeypatch.setattr(tushare_source.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="failed after 2 attempts"):
        client.fetch("daily", required=True)


def test_optional_endpoint_permission_and_retry_failure(monkeypatch) -> None:
    client, _ = _client(monkeypatch, [RuntimeError("permission denied")])
    result = client.fetch("daily", required=False)
    assert result.status == "permission_denied"
    assert result.error is not None
    client, _ = _client(
        monkeypatch,
        [RuntimeError("timeout"), RuntimeError("timeout")],
        retry=RetryPolicy(max_attempts=2, base_sleep_seconds=0.0, max_sleep_seconds=0.0, jitter_ratio=0),
    )
    monkeypatch.setattr(tushare_source.time, "sleep", lambda _: None)
    failed = client.fetch("daily", required=False)
    assert failed.status == "failed"
    assert failed.attempts == 2


def test_required_permission_error_raises(monkeypatch) -> None:
    client, _ = _client(monkeypatch, [RuntimeError("2002 permission")])
    with pytest.raises(RuntimeError, match="No permission for required endpoint"):
        client.fetch("daily", required=True)
