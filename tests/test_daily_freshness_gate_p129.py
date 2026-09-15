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
            "production": {
                "daily_run": {
                    "freshness": {
                        "min_market_coverage_ratio": 0.95,
                        "max_universe_snapshot_age_calendar_days": 45,
                    }
                }
            },
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


def _daily(date: str, symbols: tuple[str, ...]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": list(symbols),
            "trade_date": [date] * len(symbols),
            "open": [10.0] * len(symbols),
            "high": [10.1] * len(symbols),
            "low": [9.9] * len(symbols),
            "close": [10.0] * len(symbols),
            "vol": [100.0] * len(symbols),
            "amount": [1000.0] * len(symbols),
        }
    )


def _write_required(
    service: CertifiedDailySyncService,
    target: str,
    *,
    target_symbols: tuple[str, ...] = ("000001.SZ",),
    previous_symbols: tuple[str, ...] = ("000001.SZ",),
) -> None:
    previous = "20260810"
    service.store.write("daily", previous, _daily(previous, previous_symbols))
    service.store.write("daily", target, _daily(target, target_symbols))
    service.store.write(
        "adj_factor",
        target,
        pd.DataFrame(
            {
                "ts_code": list(target_symbols),
                "trade_date": [target] * len(target_symbols),
                "adj_factor": [1.0] * len(target_symbols),
            }
        ),
    )
    service.store.write(
        "daily_basic",
        target,
        pd.DataFrame(
            {
                "ts_code": list(target_symbols),
                "trade_date": [target] * len(target_symbols),
                "close": [10.0] * len(target_symbols),
            }
        ),
    )
    benchmark = service.settings.paths.metadata / "benchmarks" / "SH000300.parquet"
    benchmark.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"trade_date": pd.to_datetime([target])}).to_parquet(benchmark, index=False)


def _write_membership(settings: Settings) -> None:
    membership = settings.paths.metadata / "universe_membership" / "csi300.parquet"
    membership.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "instrument": ["SZ000001"],
            "snapshot_date": pd.to_datetime(["2026-08-01"]),
            "effective_from": pd.to_datetime(["2026-08-10"]),
            "effective_to": pd.to_datetime(["2026-08-12"]),
        }
    ).to_parquet(membership, index=False)


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

    _write_membership(settings)
    ready = service._freshness_gate(plan)
    assert ready["passed"] is True
    assert ready["universe"]["fresh"] is True
    assert ready["universe"]["active_members"] == 1
    assert ready["universe"]["snapshot_age_calendar_days"] == 10


def test_partial_target_market_is_blocked_even_when_required_tables_agree(tmp_path: Path):
    settings = _settings(tmp_path)
    service = CertifiedDailySyncService(settings)
    target = "20260811"
    previous_symbols = ("000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ")
    _write_required(
        service,
        target,
        target_symbols=("000001.SZ",),
        previous_symbols=previous_symbols,
    )
    _write_membership(settings)

    result = service._freshness_gate({"target_session": target})

    assert result["passed"] is False
    assert result["market_cross_section"]["coverage_ratio"] == 0.25
    assert result["market_cross_section"]["fresh"] is False
    assert f"market_cross_section:{target}" in result["failures"]
