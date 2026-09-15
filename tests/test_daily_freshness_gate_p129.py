from __future__ import annotations

from pathlib import Path

import pandas as pd

from qlib_platform.data.certified_daily_sync import CertifiedDailySyncService
from qlib_platform.settings import Paths, Settings


def _settings(tmp_path: Path) -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    return Settings(
        config_path=tmp_path / "configs" / "pipeline.yaml",
        data={
            "start_date": "20260810",
            "end_date": "20260811",
            "data_source": {"kind": "tushare"},
            "qlib": {
                "dataset_dir": "unused",
                "dataset_name": "test",
                "dataset_version": "test",
                "dataset_ref": "test-current",
                "include_fields": [],
            },
            "data_sync": {},
            "research": {"benchmark": "SH000300"},
            "universe": {
                "instruments": "csi300",
                "index_code": "399300.SZ",
                "membership_effective_lag_days": 1,
            },
        },
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=paths.root / "qlib" / "test",
    )


def _write_required(service: CertifiedDailySyncService, target: str) -> None:
    service.store.write(
        "daily",
        target,
        pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": [target],
                "open": [10.0],
                "high": [10.1],
                "low": [9.9],
                "close": [10.0],
                "vol": [100.0],
                "amount": [1000.0],
            }
        ),
    )
    service.store.write(
        "adj_factor",
        target,
        pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": [target], "adj_factor": [1.0]}),
    )
    service.store.write(
        "daily_basic",
        target,
        pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": [target], "close": [10.0]}),
    )
    benchmark = service.settings.paths.metadata / "benchmarks" / "SH000300.parquet"
    benchmark.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"trade_date": pd.to_datetime([target])}).to_parquet(benchmark, index=False)


def test_named_universe_must_cover_target_session(tmp_path: Path):
    settings = _settings(tmp_path)
    service = CertifiedDailySyncService(settings)
    target = "20260811"
    _write_required(service, target)
    plan = {"target_session": target}

    missing = service._freshness_gate(plan)
    assert missing["passed"] is False
    assert missing["universe"]["fresh"] is False
    assert f"universe:csi300:{target}" in missing["failures"]

    membership = settings.paths.metadata / "universe_membership" / "csi300.parquet"
    membership.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "instrument": ["SZ000001"],
            "effective_from": pd.to_datetime(["2026-08-10"]),
            "effective_to": pd.to_datetime(["2026-08-12"]),
        }
    ).to_parquet(membership, index=False)

    ready = service._freshness_gate(plan)
    assert ready["passed"] is True
    assert ready["universe"]["fresh"] is True
    assert ready["universe"]["active_members"] == 1
