from __future__ import annotations

import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

from .quality import assert_quality, validate_curated, validate_normalized, write_report
from .settings import Settings
from .store import PartitionStore, _atomic_replace, sha256_file
from .symbols import ts_to_qlib

BASIC_PERCENT_FIELDS = ["turnover_rate", "turnover_rate_f", "dv_ratio", "dv_ttm"]
SHARE_10K_FIELDS = ["total_share", "float_share", "free_share"]
MV_10K_FIELDS = ["total_mv", "circ_mv"]
MONEYFLOW_10K_FIELDS = ["buy_lg_amount", "sell_lg_amount", "buy_elg_amount", "sell_elg_amount", "net_mf_amount"]
MONEYFLOW_HAND_FIELDS = ["buy_lg_vol", "sell_lg_vol", "buy_elg_vol", "sell_elg_vol", "net_mf_vol"]


def _normalize_trade_date(value: str) -> str:
    return pd.Timestamp(value).strftime("%Y%m%d")


def _rename_daily_basic(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df.drop(columns=["close"], errors="ignore")


def _active_master(master: pd.DataFrame, trade_date: pd.Timestamp) -> pd.DataFrame:
    listed = master["list_date"].notna() & (master["list_date"] <= trade_date)
    not_delisted = master["delist_date"].isna() | (master["delist_date"] >= trade_date)
    return master.loc[listed & not_delisted].copy()


def build_curated_day(settings: Settings, trade_date: str, force: bool = False) -> Path:
    trade_date = _normalize_trade_date(trade_date)
    out_dir = settings.paths.curated / f"trade_date={trade_date}"
    out_path = out_dir / "data.parquet"
    if out_path.exists() and not force:
        return out_path

    raw = PartitionStore(settings.paths.raw)
    master_path = settings.paths.metadata / "stock_master.parquet"
    if not master_path.exists():
        raise FileNotFoundError(f"stock master not found: {master_path}")
    master = pd.read_parquet(master_path)
    master["list_date"] = pd.to_datetime(master["list_date"])
    master["delist_date"] = pd.to_datetime(master["delist_date"])
    dt = pd.Timestamp(trade_date)
    active = _active_master(master, dt)[["ts_code", "name", "list_date", "delist_date", "market", "exchange"]]
    frame = active.copy()
    frame["trade_date"] = trade_date

    daily = raw.read("daily", trade_date)
    adj = raw.read("adj_factor", trade_date)
    basic = _rename_daily_basic(raw.read("daily_basic", trade_date))
    moneyflow = raw.read("moneyflow", trade_date)
    limit_df = raw.read("stk_limit", trade_date).drop(columns=["pre_close"], errors="ignore")
    suspend = raw.read("suspend_d", trade_date)
    st = raw.read("stock_st", trade_date)

    for source in (daily, adj, basic, moneyflow, limit_df):
        if not source.empty:
            frame = frame.merge(source, on=["ts_code", "trade_date"], how="left", validate="one_to_one")

    suspended_codes = (
        set(suspend.loc[suspend.get("suspend_type", pd.Series(dtype=str)) == "S", "ts_code"])
        if not suspend.empty
        else set()
    )
    st_codes = set(st["ts_code"]) if not st.empty else set()
    frame["known_suspended"] = frame["ts_code"].isin(suspended_codes).astype(float)
    frame["paused"] = (frame["close"].isna() | frame["ts_code"].isin(suspended_codes)).astype(float)
    stock_st_manifest = raw.read_manifest("stock_st", trade_date)
    st_known = stock_st_manifest.get("status") in {"success", "empty"}
    frame["is_st"] = frame["ts_code"].isin(st_codes).astype(float) if st_known else np.nan
    frame["symbol"] = frame["ts_code"].map(ts_to_qlib)
    frame["date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d")

    report = validate_curated(frame, expected_trade_date=trade_date)
    write_report(report, settings.paths.quality / "curated" / f"{trade_date}.json")
    assert_quality(report)

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".parquet.tmp")
    frame.to_parquet(tmp, index=False)
    _atomic_replace(tmp, out_path)
    manifest = {
        "trade_date": trade_date,
        "rows": len(frame),
        "traded_rows": int(frame["close"].notna().sum()),
        "paused_rows": int(frame["paused"].sum()),
        "sha256": sha256_file(out_path),
        "source_manifests": {
            name: raw.read_manifest(name, trade_date)
            for name in ("daily", "adj_factor", "daily_basic", "moneyflow", "stk_limit", "suspend_d", "stock_st")
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        "Curated {}: active={}, traded={}, paused={}",
        trade_date,
        len(frame),
        frame["close"].notna().sum(),
        int(frame["paused"].sum()),
    )
    return out_path


def build_all_curated(settings: Settings, start_date: str | None = None, end_date: str | None = None) -> None:
    raw = PartitionStore(settings.paths.raw)
    start = _normalize_trade_date(start_date) if start_date else None
    end = _normalize_trade_date(end_date) if end_date else None
    for trade_date in raw.list_dates("daily"):
        if start and trade_date < start:
            continue
        if end and trade_date > end:
            continue
        build_curated_day(settings, trade_date)


def _load_open_calendar(settings: Settings) -> pd.DatetimeIndex:
    cal_path = settings.paths.metadata / "trade_calendar.parquet"
    if not cal_path.exists():
        raise FileNotFoundError(f"trade calendar not found: {cal_path}")
    cal = pd.read_parquet(cal_path)
    values = pd.to_datetime(cal.loc[cal["is_open"].astype(int) == 1, "cal_date"]).sort_values().unique()
    return pd.DatetimeIndex(values)


def _trading_age(date_values: pd.Series, list_date: pd.Timestamp, calendar: pd.DatetimeIndex) -> np.ndarray:
    list_idx = int(calendar.searchsorted(list_date, side="left"))
    date_idx = calendar.searchsorted(pd.DatetimeIndex(date_values), side="left")
    return np.maximum(date_idx - list_idx, 0).astype(float)


def normalize_symbol(
    df: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    base_adj_close: float | None = None,
) -> tuple[pd.DataFrame, float]:
    df = df.sort_values("date").copy()
    traded = df["close"].notna() & df["adj_factor"].notna() & (df["close"] > 0) & (df["adj_factor"] > 0)
    if base_adj_close is None:
        if not traded.any():
            raise ValueError(f"No traded row for {df['symbol'].iloc[0]}")
        first = df.loc[traded].iloc[0]
        base_adj_close = float(first["close"] * first["adj_factor"])
    if not math.isfinite(base_adj_close) or base_adj_close <= 0:
        raise ValueError("base_adj_close must be positive")

    factor = df["adj_factor"] / base_adj_close
    for col in ("open", "high", "low", "close"):
        df[col] = df[col] * factor
    # Tushare volume is in hands. Qlib adjusted volume is raw shares / factor.
    df["volume"] = df["vol"] * 100.0 / factor
    df["money"] = df["amount"] * 1000.0
    df["factor"] = factor
    df["change"] = df["pct_chg"] / 100.0
    df["vwap"] = df["money"] / df["volume"]
    df["up_limit"] = df["up_limit"] * factor if "up_limit" in df else np.nan
    df["down_limit"] = df["down_limit"] * factor if "down_limit" in df else np.nan

    for field in BASIC_PERCENT_FIELDS:
        if field in df:
            df[field] = df[field] / 100.0
    for field in SHARE_10K_FIELDS:
        if field in df:
            df[field] = df[field] * 10000.0
    for field in MV_10K_FIELDS:
        if field in df:
            df[field] = df[field] * 10000.0
    for field in MONEYFLOW_10K_FIELDS:
        if field in df:
            df[field] = df[field] * 10000.0
    for field in MONEYFLOW_HAND_FIELDS:
        if field in df:
            df[field] = df[field] * 100.0

    if all(c in df for c in ("buy_lg_amount", "sell_lg_amount", "buy_elg_amount", "sell_elg_amount")):
        df["big_net_amount"] = (
            df["buy_lg_amount"].fillna(0)
            + df["buy_elg_amount"].fillna(0)
            - df["sell_lg_amount"].fillna(0)
            - df["sell_elg_amount"].fillna(0)
        )
    else:
        df["big_net_amount"] = np.nan

    if "limit_status" in df:
        limit_status = pd.to_numeric(df["limit_status"], errors="coerce")
        df["is_limit_up"] = limit_status.isin([2, 3]).astype(float)
        df["is_limit_down"] = limit_status.isin([5, 6]).astype(float)
    else:
        raw_close = df["close"] / factor
        raw_up = df["up_limit"] / factor
        raw_down = df["down_limit"] / factor
        df["is_limit_up"] = (raw_close >= raw_up - 0.005).astype(float)
        df["is_limit_down"] = (raw_close <= raw_down + 0.005).astype(float)

    list_date = pd.Timestamp(df["list_date"].dropna().iloc[0])
    df["listed_days"] = _trading_age(df["date"], list_date, calendar)

    market_fields = ["open", "high", "low", "close", "volume", "money", "vwap", "factor", "change", "up_limit", "down_limit"]
    paused = df["paused"].fillna(1).astype(bool)
    df.loc[paused, market_fields] = np.nan

    keep = [
        "date",
        "symbol",
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
        "turnover_rate",
        "turnover_rate_f",
        "volume_ratio",
        "pe",
        "pe_ttm",
        "pb",
        "ps",
        "ps_ttm",
        "dv_ratio",
        "dv_ttm",
        "total_share",
        "float_share",
        "free_share",
        "total_mv",
        "circ_mv",
        "net_mf_amount",
        "big_net_amount",
        "up_limit",
        "down_limit",
        "is_limit_up",
        "is_limit_down",
        "is_st",
        "listed_days",
    ]
    for col in keep:
        if col not in df:
            df[col] = np.nan
    return df[keep], base_adj_close


def _curated_glob(settings: Settings) -> str:
    return str((settings.paths.curated / "trade_date=*" / "data.parquet").resolve())


def _write_staging_manifest(stage: Path, mode: str) -> Path:
    files = sorted(stage.glob("*.parquet"))
    payload = {
        "mode": mode,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "file_count": len(files),
        "files": {path.name: sha256_file(path) for path in files},
    }
    path = stage / "staging_manifest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def export_full_staging(settings: Settings, force: bool = False) -> Path:
    import duckdb

    stage = settings.paths.staging_full
    if stage.exists() and force:
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=True)
    calendar = _load_open_calendar(settings)
    con = duckdb.connect()
    glob = _curated_glob(settings)
    symbols = [row[0] for row in con.execute("SELECT DISTINCT symbol FROM read_parquet(?) ORDER BY symbol", [glob]).fetchall()]

    base_path = settings.paths.metadata / "normalization_base.parquet"
    existing_bases: dict[str, float] = {}
    if base_path.exists():
        existing_df = pd.read_parquet(base_path)
        if {"symbol", "base_adj_close"}.issubset(existing_df.columns):
            existing_bases = dict(zip(existing_df["symbol"], existing_df["base_adj_close"], strict=False))
    bases = dict(existing_bases)

    skipped: list[str] = []
    for i, symbol in enumerate(symbols, 1):
        target = stage / f"{symbol}.parquet"
        if target.exists() and not force and symbol in bases:
            continue
        raw_df = con.execute("SELECT * FROM read_parquet(?) WHERE symbol=? ORDER BY date", [glob, symbol]).df()
        try:
            norm, base = normalize_symbol(raw_df, calendar, existing_bases.get(symbol))
        except ValueError:
            if not raw_df["close"].notna().any():
                skipped.append(symbol)
                logger.warning("Skip symbol without any traded row in full stage: {}", symbol)
                continue
            raise
        report = validate_normalized(norm, symbol)
        assert_quality(report)
        norm.to_parquet(target, index=False)
        bases[symbol] = base
        if i % 200 == 0 or i == len(symbols):
            logger.info("Staging full: {}/{}", i, len(symbols))
    pd.DataFrame([{"symbol": k, "base_adj_close": v} for k, v in sorted(bases.items())]).to_parquet(base_path, index=False)
    _write_staging_manifest(stage, "full")
    return stage


def export_incremental_staging(settings: Settings, trade_dates: list[str], force: bool = True) -> Path:
    normalized_dates = [_normalize_trade_date(d) for d in trade_dates]
    stage = settings.paths.staging_update
    if stage.exists() and force:
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=True)
    calendar = _load_open_calendar(settings)
    base_path = settings.paths.metadata / "normalization_base.parquet"
    bases = pd.read_parquet(base_path).set_index("symbol")["base_adj_close"].to_dict() if base_path.exists() else {}
    frames = []
    for trade_date in normalized_dates:
        path = settings.paths.curated / f"trade_date={trade_date}" / "data.parquet"
        if not path.exists():
            raise FileNotFoundError(f"curated partition not found: {path}")
        frames.append(pd.read_parquet(path))
    all_df = pd.concat(frames, ignore_index=True)
    new_bases: list[dict[str, object]] = []
    skipped: list[str] = []
    for symbol, group in all_df.groupby("symbol", sort=True):
        try:
            norm, base = normalize_symbol(group, calendar, bases.get(symbol))
        except ValueError as exc:
            if symbol not in bases and not group["close"].notna().any():
                skipped.append(symbol)
                logger.warning("Skip new symbol without a traded row in incremental stage: {}", symbol)
                continue
            raise exc
        report = validate_normalized(norm, symbol)
        assert_quality(report)
        norm.to_parquet(stage / f"{symbol}.parquet", index=False)
        if symbol not in bases:
            new_bases.append({"symbol": symbol, "base_adj_close": base})
    if new_bases:
        merged = pd.concat(
            [
                pd.DataFrame([{"symbol": k, "base_adj_close": v} for k, v in bases.items()]),
                pd.DataFrame(new_bases),
            ],
            ignore_index=True,
        ).drop_duplicates("symbol", keep="last")
        merged.to_parquet(base_path, index=False)
    manifest = _write_staging_manifest(stage, "update")
    if skipped:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["skipped_new_symbols_without_trade"] = skipped
        manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return stage
