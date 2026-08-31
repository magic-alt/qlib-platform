from __future__ import annotations

from pathlib import Path

import pandas as pd

from tushare_qlib.client import FetchResult
from tushare_qlib.extended_parallel import FastExtendedDataBackfill
from tushare_qlib.settings import Settings
from tushare_qlib.store import PartitionStore


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def fetch(self, endpoint: str, *, required: bool, **params: str) -> FetchResult:
        self.calls.append((endpoint, params))
        if endpoint == "fina_indicator_vip":
            frame = pd.DataFrame({"ts_code": ["000001.SZ"], "revision": [2.0]})
        elif "trade_date" in params:
            frame = pd.DataFrame(
                {"ts_code": ["000001.SZ"], "trade_date": [params["trade_date"]], "value": [1.0]}
            )
        else:
            frame = pd.DataFrame({"ts_code": ["000001.SZ"], "value": [1.0]})
        return FetchResult(frame, "success", 1)


def _settings(tmp_path: Path) -> Settings:
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "project_root: ./data\n"
        "start_date: '20260801'\n"
        "end_date: '20260814'\n"
        "data_source: {kind: tushare}\n"
        "tushare: {extended_max_workers: 1}\n"
        "qlib: {dataset_dir: ./qlib}\n",
        encoding="utf-8",
    )
    return Settings.load(config, create_dirs=True)


def _open_dates(_start: str, _end: str) -> list[str]:
    return ["20260812", "20260813", "20260814"]


def test_daily_extended_gap_fills_market_and_refreshes_recent_financials(tmp_path: Path):
    settings = _settings(tmp_path)
    client = _Client()
    backfill = FastExtendedDataBackfill(
        settings,
        client=client,
        open_dates=_open_dates,
        max_workers=1,
    )
    store = PartitionStore(settings.paths.raw / "extended")
    store.write(
        "fina_indicator_vip",
        "20260630",
        pd.DataFrame({"ts_code": ["000001.SZ"], "revision": [1.0]}),
        status="success",
    )
    legacy = settings.paths.raw / "extended" / "hsgt_moneyflow"
    legacy.mkdir(parents=True)

    result = backfill.sync_daily("20260814", financial_lookback_calendar_days=80)

    assert result["market_reference"]["counters"]["changed"] == 21
    assert result["financial"]["changed_by_endpoint"]["fina_indicator_vip"] == 1
    assert store.exists("moneyflow_hsgt", "20260813")
    assert float(store.read("fina_indicator_vip", "20260630")["revision"].iloc[0]) == 2.0
    assert result["legacy_hsgt_moneyflow_removed"] is True
    assert not legacy.exists()

    market_calls = sum(1 for endpoint, _params in client.calls if endpoint == "moneyflow_hsgt")
    second = backfill.sync_daily("20260814", financial_lookback_calendar_days=80)
    second_market_calls = sum(1 for endpoint, _params in client.calls if endpoint == "moneyflow_hsgt")

    assert second["market_reference"]["counters"]["changed"] == 0
    assert second["market_reference"]["counters"]["skipped"] == 21
    assert second_market_calls == market_calls
    assert second["financial"]["counters"]["changed"] == 0
    assert second["financial"]["counters"]["success"] == 8
