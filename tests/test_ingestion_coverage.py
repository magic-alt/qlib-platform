from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from qlib_platform.data import ingestion


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.fetch_results: dict[str, object] = {}

    def call(self, name: str, **kwargs: object) -> pd.DataFrame:
        self.calls.append((name, kwargs))
        if name == "stock_basic":
            status = str(kwargs["list_status"])
            return pd.DataFrame(
                {
                    "ts_code": ["000001.SZ" if status == "L" else "600000.SH"],
                    "symbol": ["000001" if status == "L" else "600000"],
                    "name": ["PingAn" if status == "L" else "Pudong"],
                }
            )
        if name == "trade_cal":
            return pd.DataFrame(
                {
                    "exchange": ["SSE", "SSE"],
                    "cal_date": ["20260901", "20260902"],
                    "is_open": [1, 0],
                    "pretrade_date": ["20260831", "20260901"],
                }
            )
        if name == "index_daily":
            return pd.DataFrame(
                {
                    "ts_code": [kwargs["ts_code"]],
                    "trade_date": ["20260901"],
                    "close": [4000.0],
                    "open": [3990.0],
                    "high": [4010.0],
                    "low": [3980.0],
                }
            )
        raise AssertionError(name)

    def fetch(self, name: str, **kwargs: object) -> object:
        self.calls.append((name, kwargs))
        return self.fetch_results[name]


class _Store:
    def __init__(self) -> None:
        self.frames: dict[tuple[str, str], pd.DataFrame] = {}
        self.statuses: list[tuple[str, str, str]] = []
        self.terminal: set[tuple[str, str]] = set()

    def write_status(self, name: str, date: str, *, status: str, metadata: object) -> None:
        self.statuses.append((name, date, status))

    def is_terminal(self, name: str, date: str) -> bool:
        return (name, date) in self.terminal

    def write(self, name: str, date: str, frame: pd.DataFrame, metadata: object, *, status: str) -> None:
        self.frames[(name, date)] = frame.copy()
        self.statuses.append((name, date, status))

    def read(self, name: str, date: str) -> pd.DataFrame:
        return self.frames.get((name, date), pd.DataFrame())


def _settings(tmp_path: Path, *, kind: str = "tushare") -> SimpleNamespace:
    return SimpleNamespace(
        data={
            "data_source": {
                "kind": kind,
                "runtime": {"max_attempts": 2, "base_sleep_seconds": 0.1, "max_sleep_seconds": 1, "jitter_ratio": 0},
                "optional_endpoints": {"moneyflow": False},
                "mysql": {"schema": "lean_canonical_v1"},
            }
        },
        paths=SimpleNamespace(raw=tmp_path / "raw", metadata=tmp_path / "metadata", quality=tmp_path / "quality"),
    )


def _binding(client: _Client, *, mysql: bool = False, overrides: dict[str, object] | None = None, operations: dict[str, object] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        name="lean_mysql" if mysql else "tushare",
        client=client,
        capabilities={"mysql"} if mysql else set(),
        endpoint_overrides=overrides or {},
        operations=operations or {},
    )


def _result(frame: pd.DataFrame, *, succeeded: bool = True, status: str = "success", error: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(data=frame, succeeded=succeeded, status=status, attempts=1, error=error)


def test_runtime_optional_and_provider_config_helpers(tmp_path) -> None:
    settings = _settings(tmp_path)
    assert ingestion._source_runtime_config(settings)["max_attempts"] == 2
    assert ingestion._optional_endpoints(settings)["moneyflow"] is False
    assert ingestion._provider_config(settings, "mysql")["schema"] == "lean_canonical_v1"
    legacy = SimpleNamespace(data={"tushare": {"max_attempts": 5, "optional_endpoints": {"stock_st": False}}})
    assert ingestion._source_runtime_config(legacy)["max_attempts"] == 5
    assert ingestion._optional_endpoints(legacy)["stock_st"] is False
    assert ingestion._mapping(None) == {}


def test_write_parquet_atomic_replaces_file(tmp_path) -> None:
    path = tmp_path / "meta" / "x.parquet"
    ingestion._write_parquet_atomic(pd.DataFrame({"x": [1]}), path)
    ingestion._write_parquet_atomic(pd.DataFrame({"x": [2]}), path)
    assert pd.read_parquet(path)["x"].tolist() == [2]
    assert not list(path.parent.glob("*.tmp"))


def test_extractor_init_applies_endpoint_overrides(monkeypatch, tmp_path) -> None:
    client = _Client()
    override = SimpleNamespace(required=True, enabled=False)
    monkeypatch.setattr(ingestion, "create_data_source", lambda *args: _binding(client, overrides={"stock_st": override}))
    extractor = ingestion.Extractor(_settings(tmp_path))
    by_name = {item.name: item for item in extractor.endpoints}
    assert by_name["daily"].required is True
    assert by_name["moneyflow"].enabled is False
    assert by_name["stock_st"].required is True
    assert by_name["stock_st"].enabled is False
    with pytest.raises(ValueError, match="does not support operation"):
        extractor._operation("missing")


def test_fetch_stock_master_calendar_and_open_dates(monkeypatch, tmp_path) -> None:
    client = _Client()
    monkeypatch.setattr(ingestion, "create_data_source", lambda *args: _binding(client))
    extractor = ingestion.Extractor(_settings(tmp_path))
    master = extractor.fetch_stock_master()
    assert master["ts_code"].tolist() == ["000001.SZ", "600000.SH"]
    assert (extractor.settings.paths.metadata / "stock_master.parquet").is_file()
    calendar = extractor.fetch_calendar("20260901", "20260902")
    assert len(calendar) == 2
    assert extractor.open_dates("2026-09-01", "2026-09-02") == ["20260901"]


def test_fetch_day_handles_disabled_terminal_success_failure_and_required_empty(monkeypatch, tmp_path) -> None:
    client = _Client()
    monkeypatch.setattr(ingestion, "create_data_source", lambda *args: _binding(client))
    monkeypatch.setattr(ingestion, "validate_raw_day", lambda *_: {"passed": True})
    monkeypatch.setattr(ingestion, "write_report", lambda *args: None)
    monkeypatch.setattr(ingestion, "assert_quality", lambda *_: None)
    extractor = ingestion.Extractor(_settings(tmp_path))
    extractor.store = _Store()
    required_frame = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260901"]})
    for endpoint in extractor.endpoints:
        client.fetch_results[endpoint.name] = _result(required_frame.copy())
    client.fetch_results["stk_limit"] = _result(pd.DataFrame(), succeeded=False, status="failed", error="x")
    extractor.store.terminal.add(("adj_factor", "20260901"))
    extractor.store.frames[("adj_factor", "20260901")] = required_frame.copy()
    extractor.fetch_day("20260901")
    assert ("moneyflow", "20260901", "disabled") in extractor.store.statuses
    assert ("daily", "20260901") in extractor.store.frames
    assert any(item[0] == "stk_limit" and item[2] == "failed" for item in extractor.store.statuses)

    client.fetch_results["daily"] = _result(pd.DataFrame())
    with pytest.raises(RuntimeError, match="Required endpoint daily returned empty"):
        extractor.fetch_day("20260902", force=True)


def test_backfill_regular_and_mysql_preflight(monkeypatch, tmp_path) -> None:
    client = _Client()
    monkeypatch.setattr(ingestion, "create_data_source", lambda *args: _binding(client))
    extractor = ingestion.Extractor(_settings(tmp_path))
    extractor.settings.paths.metadata.mkdir(parents=True)
    (extractor.settings.paths.metadata / "stock_master.parquet").write_bytes(b"exists")
    monkeypatch.setattr(extractor, "open_dates", lambda *_: ["20260901", "20260902"])
    fetched: list[str] = []
    monkeypatch.setattr(extractor, "fetch_day", lambda date, force=False: fetched.append(date))
    extractor.backfill("20260901", "20260902")
    assert fetched == ["20260901", "20260902"]

    operations = {"preflight": lambda cfg, start, end: {"passed": False, "coverage_failures": ["gap"]}}
    monkeypatch.setattr(ingestion, "create_data_source", lambda *args: _binding(client, mysql=True, operations=operations))
    mysql = ingestion.Extractor(_settings(tmp_path, kind="lean_mysql"))
    monkeypatch.setattr(mysql, "open_dates", lambda *_: ["20260901", "20260902"])
    with pytest.raises(RuntimeError, match="coverage is incomplete"):
        mysql.backfill("20260901", "20260902")


def test_source_preflight_contract(monkeypatch, tmp_path) -> None:
    client = _Client()
    monkeypatch.setattr(ingestion, "create_data_source", lambda *args: _binding(client))
    extractor = ingestion.Extractor(_settings(tmp_path))
    with pytest.raises(ValueError, match="requires data_source.kind=lean_mysql"):
        extractor.source_preflight("20260901", "20260902")

    monkeypatch.setattr(
        ingestion,
        "create_data_source",
        lambda *args: _binding(client, mysql=True, operations={"preflight": lambda *args: {"passed": True}}),
    )
    mysql = ingestion.Extractor(_settings(tmp_path, kind="lean_mysql"))
    assert mysql.source_preflight("20260901", "20260902") == {"passed": True}
    mysql.settings.data["data_source"].pop("mysql")
    with pytest.raises(ValueError, match="mysql configuration"):
        mysql.source_preflight("20260901", "20260902")


def test_sync_benchmark_tushare_symbol_validation_and_storage(monkeypatch, tmp_path) -> None:
    client = _Client()
    monkeypatch.setattr(ingestion, "create_data_source", lambda *args: _binding(client))
    extractor = ingestion.Extractor(_settings(tmp_path))
    frame = extractor.sync_benchmark("SH000300", "20260901", "20260902")
    assert frame.iloc[0]["ts_code"] == "000300.SH"
    assert (extractor.settings.paths.metadata / "benchmarks" / "SH000300.parquet").is_file()
    with pytest.raises(ValueError, match="unsupported benchmark symbol"):
        extractor.sync_benchmark("CSI300", "20260901", "20260902")

    original = client.call
    client.call = lambda *args, **kwargs: pd.DataFrame()  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="has no index"):
        extractor.sync_benchmark("SH000300", "20260901", "20260902")
    client.call = original  # type: ignore[method-assign]
