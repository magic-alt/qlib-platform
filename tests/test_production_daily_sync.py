from __future__ import annotations

from pathlib import Path

import pandas as pd

from qlib_platform.data.production_daily_sync import ProductionDailySyncService
from qlib_platform.data.store import PartitionStore
from qlib_platform.settings import Paths, Settings


def _settings(tmp_path: Path) -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    return Settings(
        config_path=tmp_path / "configs" / "pipeline.yaml",
        data={
            "start_date": "20260807",
            "end_date": "20260811",
            "data_source": {
                "kind": "tushare",
                "optional_endpoints": {
                    "moneyflow": False,
                    "stk_limit": False,
                    "suspend_d": False,
                    "stock_st": False,
                },
            },
            "qlib": {
                "dataset_dir": "unused",
                "dataset_name": "test",
                "dataset_version": "test",
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


def _calendar(settings: Settings) -> None:
    pd.DataFrame(
        {
            "cal_date": pd.to_datetime(["2026-08-07", "2026-08-10", "2026-08-11"]),
            "is_open": [1, 1, 1],
            "pretrade_date": [pd.NaT, pd.NaT, pd.NaT],
        }
    ).to_parquet(settings.paths.metadata / "trade_calendar.parquet", index=False)


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


def _basic(date: str) -> pd.DataFrame:
    return pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": [date], "close": [10.2]})


def _factor(date: str, symbols: tuple[str, ...] = ("000001.SZ",)) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": list(symbols),
            "trade_date": [date] * len(symbols),
            "adj_factor": [1.0] * len(symbols),
        }
    )


def test_production_plan_requires_no_provider_or_token_and_reads_only_manifests(
    tmp_path: Path, monkeypatch
):
    settings = _settings(tmp_path)
    _calendar(settings)
    store = PartitionStore(settings.paths.raw)
    for date in ("20260807", "20260810", "20260811"):
        store.write("daily", date, _daily(date))
        store.write("daily_basic", date, _basic(date))
        store.write("adj_factor", date, _factor(date))

    service = ProductionDailySyncService(settings)

    def payload_read_forbidden(*args, **kwargs):
        raise AssertionError("routine planning must not read historical parquet payloads")

    monkeypatch.setattr(service.store, "read", payload_read_forbidden)
    plan = service.create_plan(as_of="2026-08-11")
    payload = plan.read_text(encoding="utf-8")

    assert '"provider_calls_during_plan": 0' in payload
    assert '"last_success": "20260811"' in payload
    # Construction and planning completed even though Settings has no Tushare token.
    assert service._extractor is None


def test_factor_cache_scans_only_dates_after_symbol_watermark(tmp_path: Path):
    settings = _settings(tmp_path)
    service = ProductionDailySyncService(settings)
    symbols = ("000001.SZ", "000002.SZ")
    for date in ("20260807", "20260810", "20260811"):
        service.store.write("adj_factor", date, _factor(date, symbols))

    service._write_factor_index("000001.SZ", _factor("20260807"), "20260807")

    histories, partition_reads = service._load_factor_histories(
        {"000001.SZ"}, staged_plan_id="watermark-test"
    )

    assert histories["000001.SZ"]["trade_date"].tolist() == ["20260807", "20260810", "20260811"]
    assert partition_reads == 2

    # The cache was advanced to the canonical watermark; a second run is metadata-only.
    histories_again, second_reads = service._load_factor_histories(
        {"000001.SZ"}, staged_plan_id="watermark-test-2"
    )
    assert histories_again["000001.SZ"]["trade_date"].tolist() == [
        "20260807",
        "20260810",
        "20260811",
    ]
    assert second_reads == 0


def test_fourteen_new_factor_indexes_scan_2500_dates_once_not_per_symbol(tmp_path: Path):
    settings = _settings(tmp_path)
    service = ProductionDailySyncService(settings)
    dates = pd.bdate_range("2016-01-04", periods=2500).strftime("%Y%m%d").tolist()
    symbols = tuple(f"{index:06d}.SZ" for index in range(1, 15))

    class CountingStore:
        def __init__(self) -> None:
            self.read_count = 0

        def list_dates(self, dataset: str) -> list[str]:
            assert dataset == "adj_factor"
            return dates

        def read(self, dataset: str, trade_date: str) -> pd.DataFrame:
            assert dataset == "adj_factor"
            self.read_count += 1
            return _factor(trade_date, symbols)

    store = CountingStore()
    service.store = store  # type: ignore[assignment]

    histories, partition_reads = service._load_factor_histories(
        set(symbols), staged_plan_id="large-history-production"
    )

    assert len(histories) == 14
    assert all(len(frame) == 2500 for frame in histories.values())
    assert partition_reads == 2500
    assert store.read_count == 2500
    assert store.read_count < 14 * 2500
