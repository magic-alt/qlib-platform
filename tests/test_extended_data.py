from __future__ import annotations

from pathlib import Path

import pandas as pd

from tushare_qlib.client import FetchResult
from tushare_qlib.extended_data import EXTENDED_ENDPOINTS
from tushare_qlib.extended_parallel import FastExtendedDataBackfill
from tushare_qlib.settings import Settings
from tushare_qlib.store import PartitionStore


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def fetch(self, endpoint: str, *, required: bool, **params: str) -> FetchResult:
        self.calls.append((endpoint, params))
        if endpoint == "stock_company":
            return FetchResult(pd.DataFrame({"ts_code": ["600000.SH"]}), "success", 1)
        if endpoint == "income_vip":
            return FetchResult(pd.DataFrame(), "permission_denied", 1, "permission")
        return FetchResult(pd.DataFrame({"ts_code": [params.get("ts_code", "600000.SH")]}), "success", 1)


def _settings(tmp_path: Path) -> Settings:
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "project_root: ./data\nstart_date: '20000101'\nend_date: '20260811'\n"
        "tushare: {}\nqlib: {dataset_dir: ./qlib}\n",
        encoding="utf-8",
    )
    return Settings.load(config, create_dirs=True)


def test_extended_backfill_records_permission_and_resumes(tmp_path: Path):
    client = _Client()
    backfill = FastExtendedDataBackfill(
        _settings(tmp_path),
        client=client,
        stock_master=pd.DataFrame({"ts_code": ["000001.SZ", "600000.SH"]}),
        open_dates=lambda _start, _end: ["20200102"],
    )

    result = backfill.backfill("20200101", "20200331", groups=["basic", "financial"])

    assert result["counters"]["success"] == 13
    assert result["counters"]["permission_denied"] == 1
    store = PartitionStore(backfill.settings.paths.raw / "extended")
    assert store.read_manifest("income_vip", "20200331")["status"] == "permission_denied"

    prior_calls = len(client.calls)
    resumed = backfill.backfill("20200101", "20200331", groups=["basic", "financial"])
    assert resumed["counters"]["skipped"] == 14
    assert len(client.calls) == prior_calls


def test_hsgt_moneyflow_uses_tushare_api_name():
    endpoint = next(item for item in EXTENDED_ENDPOINTS if item.name == "moneyflow_hsgt")

    assert endpoint.group == "market_reference"
    assert endpoint.plan == "trade_date"
    assert endpoint.min_date == "20141117"
