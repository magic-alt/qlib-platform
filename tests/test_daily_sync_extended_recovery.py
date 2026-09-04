from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from qlib_platform.daily_sync import DailySyncService
from qlib_platform.settings import Paths, Settings
from qlib_platform.store import PartitionStore


class _Extractor:
    def __init__(self) -> None:
        self.client = object()

    def open_dates(self, _start: str, _end: str) -> list[str]:
        return ["20260701", "20260702", "20260703"]


def _settings(tmp_path: Path) -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    return Settings(
        config_path=tmp_path / "configs" / "pipeline.yaml",
        data={
            "start_date": "20260101",
            "end_date": "20260814",
            "data_source": {"kind": "tushare"},
            "tushare": {},
            "qlib": {"dataset_dir": "unused", "dataset_version": "test"},
            "data_sync": {},
            "universe": {"instruments": "all"},
        },
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=paths.root / "qlib" / "test",
    )


def _financial(value: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "end_date": ["20260630"],
            "ann_date": ["20260701"],
            "roe_waa": [value],
            "roa": [0.02],
            "netprofit_margin": [0.10],
            "netprofit_yoy": [0.05],
            "or_yoy": [0.06],
            "debt_to_assets": [0.40],
            "ocf_to_or": [0.08],
        }
    )


def test_pit_source_fingerprint_rebuilds_after_financial_partition_changes(tmp_path: Path):
    settings = _settings(tmp_path)
    calendar = pd.DataFrame(
        {
            "cal_date": pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-03"]),
            "is_open": [1, 1, 1],
        }
    )
    calendar.to_parquet(settings.paths.metadata / "trade_calendar.parquet", index=False)
    extended = PartitionStore(settings.paths.raw / "extended")
    extended.write("fina_indicator_vip", "20260630", _financial(0.11), status="success")
    service = DailySyncService(settings, extractor=_Extractor())

    first, first_changed, first_symbols = service._refresh_pit_from_extended()
    second, second_changed, second_symbols = service._refresh_pit_from_extended()

    assert first["status"] == "rebuilt"
    assert first_changed is True
    assert first_symbols == {"000001.SZ"}
    assert second["status"] == "current"
    assert second_changed is False
    assert second_symbols == set()

    extended.write("fina_indicator_vip", "20260630", _financial(0.12), status="success")
    third, third_changed, third_symbols = service._refresh_pit_from_extended()

    assert third["status"] == "rebuilt"
    assert third_changed is True
    assert third_symbols == {"000001.SZ"}
    pit = pd.read_parquet(settings.paths.gold / "pit" / "current" / "fundamentals_daily.parquet")
    assert float(pit["roe_waa_pit"].dropna().iloc[-1]) == 0.12


def test_empty_pending_publish_is_normalized_to_clear(tmp_path: Path):
    settings = _settings(tmp_path)
    service = DailySyncService(settings, extractor=_Extractor())
    pending_path = settings.paths.state / "daily_sync" / "pending_publish.json"
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    pending_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "status": "pending",
                "run_id": "failed-run",
                "changed_trade_dates": [],
                "revised_symbols": [],
            }
        ),
        encoding="utf-8",
    )

    assert service._load_pending_publish() == {}
    normalized = json.loads(pending_path.read_text(encoding="utf-8"))
    assert normalized["status"] == "clear"
    assert normalized["pit_changed"] is False
    assert normalized["cleanup_reason"] == "empty_pending_state"


def test_pending_publish_keeps_pit_work_for_retry(tmp_path: Path):
    settings = _settings(tmp_path)
    service = DailySyncService(settings, extractor=_Extractor())

    service._write_pending_publish(
        run_id="run-1",
        changed_dates=[],
        revised_symbols={"000001.SZ"},
        pit_changed=True,
    )
    recovered = service._load_pending_publish()

    assert recovered["status"] == "pending"
    assert recovered["pit_changed"] is True
    assert recovered["revised_symbols"] == ["000001.SZ"]
