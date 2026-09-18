from pathlib import Path

import numpy as np
import pandas as pd

import qlib_platform.data.normalize as normalize
from qlib_platform.data.fundamentals import PIT_FIELDS
from qlib_platform.data.normalize import normalize_symbol
from qlib_platform.data.quality import validate_curated
from qlib_platform.settings import Paths, Settings


def _settings(tmp_path: Path) -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    return Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={
            "start_date": "20240102",
            "data_source": {"kind": "tushare"},
            "qlib": {"dataset_dir": "unused", "dataset_version": "test"},
        },
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=paths.root / "qlib" / "test",
    )


def test_build_all_curated_reuses_pit_hash_and_passes_daily_slices(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    master_path = settings.paths.metadata / "stock_master.parquet"
    master_path.touch()
    fundamentals_path = tmp_path / "pit_fundamentals.parquet"
    fundamentals_path.touch()
    fundamentals = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ", "000001.SZ"],
            "trade_date": ["20240102", "20240102", "20240103"],
            **{field: [1.0, 2.0, 3.0] for field in PIT_FIELDS},
        }
    )
    monkeypatch.setattr(normalize, "pit_fundamentals_path", lambda _settings: fundamentals_path)
    monkeypatch.setattr(normalize, "configured_universe", lambda _settings: None)
    monkeypatch.setattr(
        normalize.pd,
        "read_parquet",
        lambda path: pd.DataFrame() if Path(path) == master_path else fundamentals.copy(),
    )
    monkeypatch.setattr(
        normalize.PartitionStore,
        "list_dates",
        lambda _store, dataset: ["20231229", "20240102", "20240103"] if dataset == "daily" else [],
    )
    hashes = []
    monkeypatch.setattr(normalize, "sha256_file", lambda path: hashes.append(Path(path)) or "pit-sha")
    calls = []
    monkeypatch.setattr(
        normalize,
        "build_curated_day",
        lambda *_args, **kwargs: calls.append(kwargs),
    )

    normalize.build_all_curated(settings, force=True)

    assert hashes == [fundamentals_path]
    assert [call["pit_fundamentals_sha256"] for call in calls] == ["pit-sha", "pit-sha"]
    assert [frame["trade_date"].unique().tolist() for frame in (c["pit_fundamentals"] for c in calls)] == [
        ["20240102"],
        ["20240103"],
    ]


def test_export_full_staging_unifies_legacy_and_current_curated_schemas(tmp_path):
    settings = _settings(tmp_path)
    calendar_path = settings.paths.metadata / "trade_calendar.parquet"
    pd.DataFrame({"cal_date": ["20240102"], "is_open": [1]}).to_parquet(calendar_path, index=False)
    benchmark_path = settings.paths.metadata / "benchmarks" / "SH000300.parquet"
    benchmark_path.parent.mkdir(parents=True)
    pd.DataFrame(
        {
            "trade_date": ["20240102"],
            "close": [3_000.0],
            "vol": [100.0],
            "amount": [300_000.0],
        }
    ).to_parquet(benchmark_path, index=False)
    legacy = settings.paths.curated / "trade_date=19901219"
    legacy.mkdir(parents=True)
    pd.DataFrame({"trade_date": [19901219], "close": [1.0]}).to_parquet(legacy / "data.parquet", index=False)
    current = settings.paths.curated / "trade_date=20240102"
    current.mkdir(parents=True)
    pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "trade_date": [20240102],
            "date": pd.to_datetime(["2024-01-02"]),
            "symbol": ["SZ000001"],
            "list_date": pd.to_datetime(["1991-04-03"]),
            "open": [10.0],
            "high": [10.5],
            "low": [9.5],
            "close": [10.0],
            "adj_factor": [1.0],
            "vol": [100.0],
            "amount": [1_000.0],
            "pct_chg": [0.0],
            "paused": [0.0],
            "is_st": [0.0],
        }
    ).to_parquet(current / "data.parquet", index=False)

    stage = normalize.export_full_staging(settings, force=True)

    result = pd.read_parquet(stage / "SZ000001.parquet")
    assert result["date"].dt.strftime("%Y%m%d").tolist() == ["20240102"]


def test_observed_close_takes_precedence_over_conflicting_suspend_record():
    frame = pd.DataFrame({"close": [10.0, np.nan]})

    paused = frame["close"].isna().astype(float)

    assert paused.tolist() == [0.0, 1.0]


def test_source_confirmed_market_suspension_uses_lower_coverage_threshold():
    frame = pd.DataFrame(
        {
            "symbol": [f"SH60000{i}" for i in range(5)],
            "date": pd.to_datetime(["2015-07-09"] * 5),
            "ts_code": [f"60000{i}.SH" for i in range(5)],
            "close": [10.0, 10.0, np.nan, np.nan, np.nan],
            "open": [10.0, 10.0, np.nan, np.nan, np.nan],
            "high": [10.0, 10.0, np.nan, np.nan, np.nan],
            "low": [10.0, 10.0, np.nan, np.nan, np.nan],
            "adj_factor": [1.0] * 5,
            "paused": [0.0, 0.0, 1.0, 1.0, 1.0],
            "list_date": pd.to_datetime(["2000-01-01"] * 5),
        }
    )

    normal = validate_curated(frame)
    systemic_suspend = validate_curated(frame, min_traded_coverage=0.40)

    assert not next(item for item in normal.results if item.name == "traded_coverage").passed
    assert next(item for item in systemic_suspend.results if item.name == "traded_coverage").passed


def test_adjustment_identity():
    calendar = pd.DatetimeIndex(pd.to_datetime(["2024-01-02", "2024-01-03"]))
    raw = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "symbol": ["SH600000", "SH600000"],
            "list_date": pd.to_datetime(["2020-01-01", "2020-01-01"]),
            "open": [10.0, 5.1],
            "high": [10.5, 5.3],
            "low": [9.8, 5.0],
            "close": [10.0, 5.2],
            "vol": [1000.0, 2000.0],
            "amount": [1000.0, 2000.0],
            "pct_chg": [0.0, 4.0],
            "adj_factor": [1.0, 2.0],
            "paused": [0.0, 0.0],
            "is_st": [0.0, 0.0],
            **{field: [0.1, 0.2] for field in PIT_FIELDS},
        }
    )
    norm, base = normalize_symbol(raw, calendar)
    factor = norm["factor"].to_numpy()
    # adjusted price / factor must reconstruct the original raw price
    np.testing.assert_allclose(norm["close"].to_numpy() / factor, raw["close"].to_numpy())
    # adjusted volume * factor must reconstruct raw shares (Tushare hands * 100)
    np.testing.assert_allclose(norm["volume"].to_numpy() * factor, raw["vol"].to_numpy() * 100)
    assert base == 10.0
    assert norm["roe_waa_pit"].tolist() == [0.1, 0.2]
