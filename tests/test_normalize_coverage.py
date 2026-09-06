from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from qlib_platform.data import normalize as norm


def _settings(tmp_path: Path) -> SimpleNamespace:
    metadata = tmp_path / "metadata"
    paths = SimpleNamespace(
        metadata=metadata,
        curated=tmp_path / "curated",
        staging_full=tmp_path / "staging_full",
        staging_update=tmp_path / "staging_update",
        staging_repair=tmp_path / "staging_repair",
    )
    fields = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "money",
        "vwap",
        "factor",
        "change",
        "paused",
    ]
    return SimpleNamespace(paths=paths, qlib_include_fields=fields)


def _raw_symbol(symbol: str = "SZ000001", *, paused_second: bool = False) -> pd.DataFrame:
    dates = pd.to_datetime(["2026-09-01", "2026-09-02"])
    return pd.DataFrame(
        {
            "date": dates,
            "symbol": [symbol, symbol],
            "open": [10.0, 11.0],
            "high": [11.0, 12.0],
            "low": [9.0, 10.0],
            "close": [10.5, 11.5],
            "adj_factor": [2.0, 2.2],
            "vol": [100.0, 200.0],
            "amount": [1000.0, 2200.0],
            "pct_chg": [1.0, -2.0],
            "up_limit": [11.55, 12.65],
            "down_limit": [9.45, 10.35],
            "paused": [0.0, 1.0 if paused_second else 0.0],
            "list_date": [pd.Timestamp("2026-08-28")] * 2,
            "is_st": [0.0, 0.0],
            "turnover_rate": [5.0, 6.0],
            "turnover_rate_f": [4.0, 5.0],
            "dv_ratio": [2.0, 3.0],
            "dv_ttm": [1.0, 1.5],
            "total_share": [10.0, 10.0],
            "float_share": [8.0, 8.0],
            "free_share": [7.0, 7.0],
            "total_mv": [100.0, 110.0],
            "circ_mv": [80.0, 88.0],
            "buy_lg_amount": [1.0, 2.0],
            "sell_lg_amount": [0.5, 1.0],
            "buy_elg_amount": [0.3, 0.4],
            "sell_elg_amount": [0.1, 0.2],
            "net_mf_amount": [0.7, 1.2],
            "buy_lg_vol": [2.0, 3.0],
            "sell_lg_vol": [1.0, 1.5],
            "buy_elg_vol": [0.5, 0.6],
            "sell_elg_vol": [0.2, 0.3],
            "net_mf_vol": [1.3, 1.8],
            "limit_status": [2, 6],
            "volume_ratio": [1.1, 1.2],
            "pe": [10.0, 11.0],
            "pe_ttm": [10.5, 11.5],
            "pb": [1.0, 1.1],
            "ps": [2.0, 2.1],
            "ps_ttm": [2.2, 2.3],
        }
    )


def test_small_normalization_helpers() -> None:
    assert norm._normalize_trade_date("2026-09-01") == "20260901"
    frame = pd.DataFrame({"close": [1.0], "x": [2]})
    assert list(norm._rename_daily_basic(frame).columns) == ["x"]
    assert norm._rename_daily_basic(pd.DataFrame()).empty

    master = pd.DataFrame(
        {
            "ts_code": ["a", "b", "c"],
            "list_date": pd.to_datetime(["2026-01-01", "2026-10-01", "2026-01-01"]),
            "delist_date": [pd.NaT, pd.NaT, pd.Timestamp("2026-08-01")],
        }
    )
    active = norm._active_master(master, pd.Timestamp("2026-09-01"))
    assert active["ts_code"].tolist() == ["a"]


def test_normalize_symbol_converts_units_limits_and_paused_rows() -> None:
    calendar = pd.date_range("2026-08-28", "2026-09-03", freq="D")
    result, base = norm.normalize_symbol(_raw_symbol(paused_second=True), calendar)
    assert base == pytest.approx(21.0)
    assert result.loc[0, "turnover_rate"] == pytest.approx(0.05)
    assert result.loc[0, "total_share"] == pytest.approx(100_000.0)
    assert result.loc[0, "total_mv"] == pytest.approx(1_000_000.0)
    assert result.loc[0, "net_mf_amount"] == pytest.approx(7000.0)
    assert result.loc[0, "is_limit_up"] == 1.0
    assert result.loc[1, "is_limit_down"] == 1.0
    assert result.loc[0, "big_net_amount"] == pytest.approx(7000.0)
    assert np.isnan(result.loc[1, "close"])
    assert result.loc[0, "listed_days"] >= 0


def test_normalize_symbol_derives_limits_without_status_and_validates_base() -> None:
    frame = _raw_symbol().drop(
        columns=["limit_status", "buy_lg_amount", "sell_lg_amount", "buy_elg_amount", "sell_elg_amount"]
    )
    frame.loc[0, "close"] = frame.loc[0, "up_limit"]
    frame.loc[1, "close"] = frame.loc[1, "down_limit"]
    calendar = pd.date_range("2026-08-28", "2026-09-03", freq="D")
    result, _ = norm.normalize_symbol(frame, calendar, base_adj_close=20.0)
    assert result["is_limit_up"].tolist() == [1.0, 0.0]
    assert result["is_limit_down"].tolist() == [0.0, 1.0]
    assert result["big_net_amount"].isna().all()
    with pytest.raises(ValueError, match="base_adj_close"):
        norm.normalize_symbol(frame, calendar, base_adj_close=0.0)
    no_trade = frame.copy()
    no_trade[["close", "adj_factor"]] = np.nan
    with pytest.raises(ValueError, match="No traded row"):
        norm.normalize_symbol(no_trade, calendar)


def test_open_calendar_and_benchmark_staging_frame(tmp_path) -> None:
    settings = _settings(tmp_path)
    settings.paths.metadata.mkdir(parents=True)
    pd.DataFrame({"cal_date": ["2026-09-01", "2026-09-02", "2026-09-03"], "is_open": [1, 0, 1]}).to_parquet(
        settings.paths.metadata / "trade_calendar.parquet", index=False
    )
    calendar = norm._load_open_calendar(settings)
    assert calendar.tolist() == [pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-03")]

    benchmark_dir = settings.paths.metadata / "benchmarks"
    benchmark_dir.mkdir()
    pd.DataFrame(
        {
            "trade_date": ["2026-09-01", "2026-09-03", "2026-09-03"],
            "close": [4000.0, 4100.0, 4110.0],
            "open": [3990.0, np.nan, 4090.0],
            "vol": [10.0, 20.0, 30.0],
            "amount": [100.0, 200.0, 300.0],
            "pct_chg": [0.5, 1.0, 1.1],
        }
    ).to_parquet(benchmark_dir / "SH000300.parquet", index=False)
    frame = norm._benchmark_staging_frame(settings, calendar)
    assert frame["symbol"].unique().tolist() == ["SH000300"]
    assert len(frame) == 2
    assert frame.iloc[-1]["close"] == pytest.approx(4110.0)
    assert frame.iloc[-1]["high"] == pytest.approx(4110.0)
    assert frame.iloc[0]["volume"] == pytest.approx(1000.0)

    (benchmark_dir / "SH000300.parquet").unlink()
    with pytest.raises(FileNotFoundError, match="benchmark data"):
        norm._benchmark_staging_frame(settings, calendar)
    pd.DataFrame({"trade_date": ["2026-09-01"]}).to_parquet(benchmark_dir / "SH000300.parquet", index=False)
    with pytest.raises(ValueError, match="missing columns"):
        norm._benchmark_staging_frame(settings, calendar)
    pd.DataFrame({"trade_date": ["2026-09-01"], "close": [-1.0]}).to_parquet(
        benchmark_dir / "SH000300.parquet", index=False
    )
    with pytest.raises(ValueError, match="positive"):
        norm._benchmark_staging_frame(settings, calendar)


def test_staging_file_helpers(tmp_path, monkeypatch) -> None:
    stage = tmp_path / "stage"
    target = stage / "SZ000001.parquet"
    frame = pd.DataFrame({"x": [1, 2]})
    norm._write_stage_parquet(frame, target)
    assert pd.read_parquet(target)["x"].tolist() == [1, 2]
    manifest = norm._write_staging_manifest(stage, "test")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["file_count"] == 1
    assert payload["mode"] == "test"
    assert "SZ000001.parquet" in payload["files"]

    norm._remove_staging_tree(stage)
    assert not stage.exists()

    attempts = 0
    original = norm.shutil.rmtree

    def flaky(path: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OSError("busy")
        original(path)

    stage.mkdir()
    monkeypatch.setattr(norm.shutil, "rmtree", flaky)
    monkeypatch.setattr(norm.time, "sleep", lambda _: None)
    norm._remove_staging_tree(stage)
    assert attempts == 3


def test_incremental_staging_writes_symbols_benchmark_bases_and_skip_manifest(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    settings.paths.metadata.mkdir(parents=True)
    settings.paths.curated.mkdir(parents=True)
    calendar = pd.date_range("2026-08-28", "2026-09-03", freq="D")
    monkeypatch.setattr(norm, "_load_open_calendar", lambda _: calendar)
    monkeypatch.setattr(norm, "validate_normalized", lambda frame, symbol: {"ok": True})
    monkeypatch.setattr(norm, "assert_quality", lambda report: None)
    benchmark = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-09-01", "2026-09-02"]),
            "symbol": ["SH000300", "SH000300"],
            **{field: [1.0, 1.0] for field in settings.qlib_include_fields},
        }
    )
    monkeypatch.setattr(norm, "_benchmark_staging_frame", lambda *_: benchmark.copy())

    good = _raw_symbol().iloc[[0]].copy()
    bad = _raw_symbol("SZ000002").iloc[[0]].copy()
    bad["close"] = np.nan
    bad["adj_factor"] = np.nan
    source = pd.concat([good, bad], ignore_index=True)
    day = settings.paths.curated / "trade_date=20260901"
    day.mkdir()
    source.to_parquet(day / "data.parquet", index=False)

    output = norm.export_incremental_staging(settings, ["2026-09-01"])
    assert (output / "SZ000001.parquet").is_file()
    assert (output / "SH000300.parquet").is_file()
    assert (settings.paths.metadata / "normalization_base.parquet").is_file()
    payload = json.loads((output / "staging_manifest.json").read_text(encoding="utf-8"))
    assert payload["mode"] == "update"
    assert payload["skipped_new_symbols_without_trade"] == ["SZ000002"]

    with pytest.raises(FileNotFoundError, match="curated partition"):
        norm.export_incremental_staging(settings, ["2026-09-03"])


def test_incremental_staging_requires_benchmark_coverage(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    settings.paths.metadata.mkdir(parents=True)
    day = settings.paths.curated / "trade_date=20260901"
    day.mkdir(parents=True)
    _raw_symbol().iloc[[0]].to_parquet(day / "data.parquet", index=False)
    monkeypatch.setattr(norm, "_load_open_calendar", lambda _: pd.date_range("2026-09-01", periods=2))
    monkeypatch.setattr(norm, "validate_normalized", lambda *_: {})
    monkeypatch.setattr(norm, "assert_quality", lambda *_: None)
    monkeypatch.setattr(
        norm,
        "_benchmark_staging_frame",
        lambda *_: pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-09-02"]),
                "symbol": ["SH000300"],
                **{field: [1.0] for field in settings.qlib_include_fields},
            }
        ),
    )
    with pytest.raises(ValueError, match="benchmark does not cover"):
        norm.export_incremental_staging(settings, ["2026-09-01"])
