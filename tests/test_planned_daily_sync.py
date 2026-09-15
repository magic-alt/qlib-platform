from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from qlib_platform.data.planned_daily_sync import PlannedDailySyncService, factor_history_diff
from qlib_platform.data.sources import FetchResult
from qlib_platform.data.store import PartitionStore
from qlib_platform.settings import Paths, Settings


def _settings(tmp_path: Path) -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    return Settings(
        config_path=tmp_path / "configs" / "pipeline.yaml",
        data={
            "start_date": "20260803",
            "end_date": "20260811",
            "data_source": {"kind": "tushare"},
            "tushare": {},
            "qlib": {
                "dataset_dir": "unused",
                "dataset_version": "test",
                "dataset_name": "test",
                "dataset_ref": "test-current",
                "include_fields": ["open", "high", "low", "close", "volume", "factor"],
            },
            "data_sync": {
                "timezone": "Asia/Shanghai",
                "ready_after": "17:30",
                "market_lookback_trading_days": 2,
                "market_catchup_trading_days": 10,
                "corporate_action_lookback_calendar_days": 2,
            },
            "universe": {"instruments": "all"},
        },
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=paths.root / "qlib" / "test",
    )


def _calendar(settings: Settings, dates: list[str]) -> None:
    frame = pd.DataFrame(
        {
            "cal_date": pd.to_datetime(dates),
            "is_open": [1] * len(dates),
            "pretrade_date": [pd.NaT] * len(dates),
        }
    )
    frame.to_parquet(settings.paths.metadata / "trade_calendar.parquet", index=False)


def _daily(date: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "trade_date": [date],
            "open": [10.0],
            "high": [10.5],
            "low": [9.8],
            "close": [10.2],
            "vol": [100.0],
            "amount": [1000.0],
        }
    )


def _factor(date: str, value: float = 1.0, symbol: str = "000001.SZ") -> pd.DataFrame:
    return pd.DataFrame({"ts_code": [symbol], "trade_date": [date], "adj_factor": [value]})


def _basic(date: str) -> pd.DataFrame:
    return pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": [date], "close": [10.2]})


@dataclass(frozen=True)
class _Endpoint:
    name: str
    fields: str
    required: bool = True
    enabled: bool = True


class _Client:
    def __init__(self) -> None:
        self.fetch_calls = 0
        self.call_calls = 0

    def fetch(self, endpoint: str, **kwargs):
        self.fetch_calls += 1
        date = str(kwargs["trade_date"])
        frame = {
            "daily": _daily(date),
            "adj_factor": _factor(date),
            "daily_basic": _basic(date),
        }[endpoint]
        return FetchResult(frame, "success", 1)

    def call(self, endpoint: str, **kwargs):
        self.call_calls += 1
        raise AssertionError(f"unexpected provider call: {endpoint}")


class _Extractor:
    endpoints = [
        _Endpoint("daily", "daily"),
        _Endpoint("adj_factor", "factor"),
        _Endpoint("daily_basic", "basic"),
    ]

    def __init__(self, client: _Client | None = None) -> None:
        self.client = client or _Client()


def test_plan_is_network_free_and_catches_endpoint_specific_gap(tmp_path: Path):
    settings = _settings(tmp_path)
    dates = ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-10", "2026-08-11"]
    _calendar(settings, dates)
    store = PartitionStore(settings.paths.raw)
    for date in [value.replace("-", "") for value in dates]:
        store.write("daily", date, _daily(date))
        store.write("daily_basic", date, _basic(date))
        if date != "20260805":
            store.write("adj_factor", date, _factor(date))

    client = _Client()
    service = PlannedDailySyncService(settings, extractor=_Extractor(client))
    plan_path = service.create_plan(as_of="2026-08-11")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    assert client.fetch_calls == 0
    assert client.call_calls == 0
    assert plan["provider_calls_during_plan"] == 0
    assert plan["endpoint_gaps"]["daily"] == []
    assert plan["endpoint_gaps"]["adj_factor"] == ["20260805"]
    assert "adj_factor" in plan["fetch_matrix"]["20260805"]
    # Routine overlap remains bounded to the configured two latest sessions.
    assert set(plan["fetch_matrix"]["20260810"]) == {"daily", "adj_factor", "daily_basic"}
    assert set(plan["fetch_matrix"]["20260811"]) == {"daily", "adj_factor", "daily_basic"}


def test_staged_market_fetch_is_partition_resumable_without_repeat_calls(tmp_path: Path):
    settings = _settings(tmp_path)
    _calendar(settings, ["2026-08-10", "2026-08-11"])
    client = _Client()
    service = PlannedDailySyncService(settings, extractor=_Extractor(client))
    plan = {
        "plan_id": "syncplan-test",
        "fetch_matrix": {"20260811": ["daily", "adj_factor", "daily_basic"]},
    }
    state = {"plan_id": "syncplan-test", "steps": {}, "context": {}}

    service._fetch_market_to_stage(plan, state)
    assert client.fetch_calls == 3

    # Simulate a crash after durable partitions were written but before the node
    # completion record survived.  The retry must reuse all three stage partitions.
    state["steps"].pop("market_fetch")
    service._fetch_market_to_stage(plan, state)
    assert client.fetch_calls == 3


def test_factor_history_diff_returns_only_true_revision_dates():
    old = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * 3,
            "trade_date": ["20260807", "20260810", "20260811"],
            "adj_factor": [1.0, 1.0, 1.1],
        }
    )
    new = old.copy()
    new.loc[new["trade_date"] == "20260810", "adj_factor"] = 1.05

    assert factor_history_diff(old, new) == ["20260810"]


class _CountingFactorStore:
    def __init__(self, dates: list[str], symbols: list[str]) -> None:
        self.dates = dates
        self.symbols = symbols
        self.read_count = 0

    def list_dates(self, dataset: str) -> list[str]:
        assert dataset == "adj_factor"
        return self.dates

    def read(self, dataset: str, trade_date: str) -> pd.DataFrame:
        assert dataset == "adj_factor"
        self.read_count += 1
        return pd.DataFrame(
            {
                "ts_code": self.symbols,
                "trade_date": [trade_date] * len(self.symbols),
                "adj_factor": [1.0] * len(self.symbols),
            }
        )


def test_factor_index_build_reads_history_once_not_symbols_times_history(tmp_path: Path):
    settings = _settings(tmp_path)
    _calendar(settings, ["2026-08-11"])
    service = PlannedDailySyncService(settings, extractor=_Extractor())
    dates = pd.bdate_range("2016-01-04", periods=2500).strftime("%Y%m%d").tolist()
    symbols = [f"{index:06d}.SZ" for index in range(1, 15)]
    counting = _CountingFactorStore(dates, symbols)
    service.store = counting  # type: ignore[assignment]

    histories, partition_reads = service._load_factor_histories(
        set(symbols),
        staged_plan_id="large-history",
    )

    assert len(histories) == 14
    assert all(len(frame) == 2500 for frame in histories.values())
    assert partition_reads == 2500
    assert counting.read_count == 2500
    # The forbidden old shape would have been 14 * 2500 = 35,000 reads.
    assert counting.read_count < len(symbols) * len(dates)


def test_non_trading_day_plan_is_explicit_skip(tmp_path: Path):
    settings = _settings(tmp_path)
    frame = pd.DataFrame(
        {
            "cal_date": pd.to_datetime(["2026-08-14", "2026-08-15", "2026-08-16"]),
            "is_open": [1, 0, 0],
            "pretrade_date": [pd.NaT, pd.NaT, pd.NaT],
        }
    )
    frame.to_parquet(settings.paths.metadata / "trade_calendar.parquet", index=False)
    service = PlannedDailySyncService(settings, extractor=_Extractor())

    plan = json.loads(service.create_plan(as_of="2026-08-15").read_text(encoding="utf-8"))

    assert plan["status"] == "SKIPPED_NON_TRADING_DAY"
    assert plan["fetch_matrix"] == {}
