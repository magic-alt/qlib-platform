from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from qlib_platform.data.sources import FetchResult
from qlib_platform.data.corporate_actions import CorporateActionStore
from qlib_platform import daily_sync, qlib_export
from qlib_platform.data.daily_sync import DailySyncService, SingleInstanceLock
from qlib_platform.data.ingestion import Extractor
from qlib_platform.data.kline_export import build_kline
from qlib_platform.data.quality import QualityResult, make_report
from qlib_platform.settings import Paths, Settings
from qlib_platform.data.store import PartitionStore


def _settings(tmp_path: Path) -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    return Settings(
        config_path=tmp_path / "configs" / "pipeline.yaml",
        data={
            "start_date": "20260807",
            "end_date": "20260810",
            "data_source": {"kind": "tushare"},
            "tushare": {},
            "qlib": {
                "dataset_dir": "unused",
                "dataset_version": "test",
                "include_fields": ["open", "high", "low", "close", "volume", "factor"],
            },
            "data_sync": {
                "market_lookback_trading_days": 2,
                "corporate_action_lookback_calendar_days": 2,
            },
            "universe": {"instruments": "all"},
        },
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=paths.root / "qlib" / "test",
    )


def _daily(date: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "trade_date": [date],
            "open": [10.0],
            "high": [10.5],
            "low": [9.8],
            "close": [10.2],
            "pre_close": [10.0],
            "change": [0.2],
            "pct_chg": [2.0],
            "vol": [100.0],
            "amount": [1000.0],
        }
    )


def _factor(date: str, value: float = 1.0) -> pd.DataFrame:
    return pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": [date], "adj_factor": [value]})


def _basic(date: str) -> pd.DataFrame:
    return pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": [date], "close": [10.2]})


@dataclass(frozen=True)
class _Endpoint:
    name: str
    fields: str
    required: bool = True
    enabled: bool = True


class _Client:
    def fetch(self, endpoint, **kwargs):
        date = kwargs["trade_date"]
        frame = {"daily": _daily(date), "adj_factor": _factor(date), "daily_basic": _basic(date)}[endpoint]
        return FetchResult(frame, "success", 1)

    def call(self, endpoint, **kwargs):
        assert endpoint == "dividend"
        return pd.DataFrame()


class _Extractor:
    endpoints = [
        _Endpoint("daily", "daily"),
        _Endpoint("adj_factor", "factor"),
        _Endpoint("daily_basic", "basic"),
    ]

    def __init__(self):
        self.client = _Client()

    def open_dates(self, start, end):
        return ["20260807", "20260810"]


def test_check_only_detects_changes_without_promoting_raw(tmp_path: Path):
    settings = _settings(tmp_path)
    service = DailySyncService(settings, extractor=_Extractor())

    manifest_path = service.run(as_of="20260810", check_only=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["status"] == "checked"
    assert manifest["changed_trade_dates"] == ["20260807", "20260810"]
    assert not PartitionStore(settings.paths.raw).exists("daily", "20260810")


def test_open_dates_orders_descending_provider_calendar(tmp_path: Path):
    settings = _settings(tmp_path)
    calendar = pd.DataFrame(
        {
            "cal_date": pd.to_datetime(["2026-08-10", "2026-08-09", "2026-08-07"]),
            "is_open": [1, 0, 1],
        }
    )
    calendar.to_parquet(settings.paths.metadata / "trade_calendar.parquet", index=False)

    extractor = object.__new__(Extractor)
    extractor.settings = settings
    dates = extractor.open_dates("20260807", "20260810")

    assert dates == ["20260807", "20260810"]


def test_market_dates_backfills_recent_raw_gap_beyond_lookback(tmp_path: Path):
    settings = _settings(tmp_path)
    extractor = _Extractor()
    dates = [
        "20260803",
        "20260804",
        "20260805",
        "20260806",
        "20260807",
        "20260810",
        "20260811",
    ]
    extractor.open_dates = lambda start, end: dates
    store = PartitionStore(settings.paths.raw)
    for date in ("20260803", "20260804", "20260807", "20260810", "20260811"):
        store.write("daily", date, _daily(date))
    service = DailySyncService(settings, extractor=extractor)

    assert service._market_dates(pd.Timestamp("2026-08-11")) == [
        "20260805",
        "20260806",
        "20260810",
        "20260811",
    ]


def test_dividend_failure_does_not_promote_market_data(tmp_path: Path):
    settings = _settings(tmp_path)
    extractor = _Extractor()

    def fail_dividend(endpoint, **kwargs):
        raise RuntimeError("dividend unavailable")

    extractor.client.call = fail_dividend
    service = DailySyncService(settings, extractor=extractor)

    with pytest.raises(RuntimeError, match="dividend unavailable"):
        service.run(as_of="20260810")

    assert not PartitionStore(settings.paths.raw).exists("daily", "20260810")


def test_raw_integrity_failure_keeps_publish_pending_and_blocks_qlib(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    service = DailySyncService(settings, extractor=_Extractor())
    monkeypatch.setattr(
        daily_sync,
        "validate_raw_store",
        lambda *args, **kwargs: make_report(
            "raw_store:current",
            [QualityResult("daily_calendar_coverage", False, "missing=['20260807']")],
        ),
    )
    monkeypatch.setattr(
        service,
        "_refresh_metadata",
        lambda dates: pytest.fail("metadata refresh must not run after integrity failure"),
    )

    with pytest.raises(AssertionError, match="daily_calendar_coverage"):
        service.run(as_of="20260810")

    pending_path = settings.paths.state / "daily_sync" / "pending_publish.json"
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    assert pending["status"] == "pending"
    assert pending["changed_trade_dates"] == ["20260807", "20260810"]


def test_factor_jump_detection_and_single_instance_lock(tmp_path: Path):
    settings = _settings(tmp_path)
    service = DailySyncService(settings, extractor=_Extractor())
    staged = {
        ("adj_factor", "20260807"): _factor("20260807", 1.0),
        ("adj_factor", "20260810"): _factor("20260810", 1.1),
    }
    assert service._factor_event_symbols(["20260807", "20260810"], staged) == {"000001.SZ"}

    lock_path = tmp_path / "daily-sync.lock"
    with SingleInstanceLock(lock_path):
        with pytest.raises(RuntimeError, match="already running"):
            with SingleInstanceLock(lock_path):
                pass


def test_factor_history_reconcile_bootstraps_missing_partitions(tmp_path: Path):
    settings = _settings(tmp_path)
    extractor = _Extractor()
    history = pd.concat(
        [_factor("20260807", 1.0), _factor("20260810", 1.1)],
        ignore_index=True,
    )

    def factor_history(endpoint, **kwargs):
        assert endpoint == "adj_factor"
        assert kwargs["ts_code"] == "000001.SZ"
        return history.copy()

    extractor.client.call = factor_history
    service = DailySyncService(settings, extractor=extractor)
    staged: dict[tuple[str, str], pd.DataFrame] = {}
    metadata: dict[tuple[str, str], dict[str, object]] = {}

    service._reconcile_factor_histories({"000001.SZ"}, staged, metadata, "20260810")

    assert staged[("adj_factor", "20260807")].to_dict("records") == _factor("20260807", 1.0).to_dict(
        "records"
    )
    assert staged[("adj_factor", "20260810")].to_dict("records") == _factor("20260810", 1.1).to_dict(
        "records"
    )
    assert metadata[("adj_factor", "20260807")]["sync_mode"] == "factor_history_reconcile"


def test_dividend_upsert_replaces_changed_record_in_current(tmp_path: Path):
    settings = _settings(tmp_path)
    store = CorporateActionStore(settings)
    first = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "end_date": ["20251231"],
            "ann_date": ["20260401"],
            "div_proc": ["实施"],
            "cash_div": [0.1],
            "ex_date": ["20260601"],
        }
    )
    assert store.upsert(first)["changed_symbol_count"] == 1
    revised = first.copy()
    revised["cash_div"] = 0.2
    assert store.upsert(revised)["changed_symbol_count"] == 1
    assert not (settings.paths.bronze / "revisions").exists()
    assert float(store.read("000001.SZ")["cash_div"].iloc[0]) == 0.2


def test_local_kline_adjustment_modes(tmp_path: Path):
    settings = _settings(tmp_path)
    raw = PartitionStore(settings.paths.raw)
    for date, close, factor in (("20260807", 10.0, 1.0), ("20260810", 5.2, 2.0)):
        daily = _daily(date)
        daily[["open", "high", "low", "close", "pre_close"]] = close
        raw.write("daily", date, daily)
        raw.write("adj_factor", date, _factor(date, factor))

    qfq = build_kline(settings, "000001.SZ", adjustment="qfq")
    hfq = build_kline(settings, "SZ000001", adjustment="hfq")
    raw_frame = build_kline(settings, "SZ000001", adjustment="raw")

    assert qfq["close"].tolist() == pytest.approx([5.0, 5.2])
    assert hfq["close"].tolist() == pytest.approx([10.0, 10.4])
    assert raw_frame["close"].tolist() == pytest.approx([10.0, 5.2])


def test_full_publish_keeps_old_dataset_when_fingerprint_fails(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    target = settings.qlib_data_uri
    target.mkdir(parents=True)
    sentinel = target / "old.txt"
    sentinel.write_text("old", encoding="utf-8")

    monkeypatch.setattr(qlib_export, "_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(qlib_export, "install_qlib_universe", lambda *args, **kwargs: None)
    monkeypatch.setattr(qlib_export, "_smoke_test_dataset_subprocess", lambda *args: {"ok": True})

    def fail_fingerprint(*args, **kwargs):
        raise RuntimeError("fingerprint failed")

    monkeypatch.setattr(qlib_export, "write_fingerprint", fail_fingerprint)

    with pytest.raises(RuntimeError, match="fingerprint failed"):
        qlib_export.dump_full(settings)

    assert sentinel.read_text(encoding="utf-8") == "old"
    assert not list(target.parent.glob(f".{target.name}.building.*"))
    assert not list(target.parent.glob(f"{target.name}.backup.*"))


def test_sync_calendar_rejects_changed_date_gap(tmp_path: Path):
    candidate = tmp_path / "candidate"
    calendar = candidate / "calendars" / "day.txt"
    calendar.parent.mkdir(parents=True)
    calendar.write_text("2026-08-14\n2026-08-24\n2026-08-25\n", encoding="utf-8")

    with pytest.raises(ValueError, match="2026-08-17"):
        qlib_export._validate_sync_calendar(
            candidate,
            {"changed_trade_dates": ["20260817", "20260824"]},
        )
