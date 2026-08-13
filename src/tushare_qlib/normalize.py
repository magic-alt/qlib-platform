from __future__ import annotations

import json
import math
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd
from loguru import logger

from .fundamentals import PIT_FIELDS, pit_fundamentals_path
from .quality import assert_quality, validate_curated, validate_normalized, write_report
from .settings import Settings
from .store import PartitionStore, _atomic_replace, sha256_file
from .symbols import ts_to_qlib
from .universe import configured_universe

BASIC_PERCENT_FIELDS = ["turnover_rate", "turnover_rate_f", "dv_ratio", "dv_ttm"]
SHARE_10K_FIELDS = ["total_share", "float_share", "free_share"]
MV_10K_FIELDS = ["total_mv", "circ_mv"]
MONEYFLOW_10K_FIELDS = [
    "buy_lg_amount",
    "sell_lg_amount",
    "buy_elg_amount",
    "sell_elg_amount",
    "net_mf_amount",
]
MONEYFLOW_HAND_FIELDS = ["buy_lg_vol", "sell_lg_vol", "buy_elg_vol", "sell_elg_vol", "net_mf_vol"]


def _normalize_trade_date(value: str) -> str:
    return str(pd.Timestamp(value).strftime("%Y%m%d"))


def _rename_daily_basic(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df.drop(columns=["close"], errors="ignore")


def _active_master(master: pd.DataFrame, trade_date: pd.Timestamp) -> pd.DataFrame:
    listed = master["list_date"].notna() & (master["list_date"] <= trade_date)
    not_delisted = master["delist_date"].isna() | (master["delist_date"] >= trade_date)
    return master.loc[listed & not_delisted].copy()


def _restrict_lean_active_universe(
    active: pd.DataFrame,
    settings: Settings,
    trade_date: pd.Timestamp,
    membership: pd.DataFrame | None = None,
) -> pd.DataFrame:
    source_cfg = settings.data.get("data_source", {})
    mysql_cfg = source_cfg.get("mysql", {}) if isinstance(source_cfg, dict) else {}
    if settings.uses_tushare_source() or str(mysql_cfg.get("schema", "")).lower() != "lean_canonical_v1":
        return active
    configured = configured_universe(settings)
    if configured is None:
        return active
    membership_path = configured[2]
    if membership is None:
        if not membership_path.is_file():
            raise FileNotFoundError(
                f"PIT universe membership is required before MySQL curation: {membership_path}; "
                "run sync-universe first"
            )
        membership = pd.read_parquet(membership_path)
    required = {"instrument", "effective_from", "effective_to"}
    missing = required - set(membership.columns)
    if missing:
        raise ValueError(f"universe membership missing columns: {sorted(missing)}")
    effective_from = pd.to_datetime(membership["effective_from"], errors="raise").dt.normalize()
    effective_to = pd.to_datetime(membership["effective_to"], errors="raise").dt.normalize()
    members = set(
        membership.loc[
            (effective_from <= trade_date.normalize()) & (effective_to >= trade_date.normalize()),
            "instrument",
        ].astype(str)
    )
    if not members:
        raise ValueError(f"PIT universe has no active members on {trade_date:%Y-%m-%d}")
    instruments = active["ts_code"].astype(str).map(ts_to_qlib)
    return active.loc[instruments.isin(members)].copy()


def _merge_pit_fundamentals(
    frame: pd.DataFrame,
    settings: Settings,
    trade_date: str,
    fundamentals: pd.DataFrame | None = None,
) -> pd.DataFrame:
    path = pit_fundamentals_path(settings)
    if not path.exists():
        result = frame.copy()
        for field in PIT_FIELDS:
            result[field] = np.nan
        return result
    fundamentals = pd.read_parquet(path) if fundamentals is None else fundamentals
    required = {"ts_code", "trade_date", *PIT_FIELDS}
    missing = required - set(fundamentals.columns)
    if missing:
        raise ValueError(f"PIT fundamentals missing columns: {sorted(missing)}")
    fundamentals = fundamentals[["ts_code", "trade_date", *PIT_FIELDS]].copy()
    fundamentals["trade_date"] = pd.to_datetime(fundamentals["trade_date"], errors="raise").dt.strftime(
        "%Y%m%d"
    )
    fundamentals = fundamentals.loc[fundamentals["trade_date"] == trade_date]
    if fundamentals.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError(f"duplicate PIT fundamentals for trade_date={trade_date}")
    return frame.merge(fundamentals, on=["ts_code", "trade_date"], how="left", validate="one_to_one")


def build_curated_day(
    settings: Settings,
    trade_date: str,
    force: bool = False,
    *,
    master: pd.DataFrame | None = None,
    pit_fundamentals: pd.DataFrame | None = None,
    universe_membership: pd.DataFrame | None = None,
) -> Path:
    trade_date = _normalize_trade_date(trade_date)
    out_dir = settings.paths.curated / f"trade_date={trade_date}"
    out_path = out_dir / "data.parquet"
    if out_path.exists() and not force:
        return out_path

    raw = PartitionStore(settings.paths.raw)
    master_path = settings.paths.metadata / "stock_master.parquet"
    if not master_path.exists():
        raise FileNotFoundError(f"stock master not found: {master_path}")
    master = pd.read_parquet(master_path) if master is None else master.copy()
    master["list_date"] = pd.to_datetime(master["list_date"])
    master["delist_date"] = pd.to_datetime(master["delist_date"])
    dt = pd.Timestamp(trade_date)
    active = _active_master(master, dt)
    active = _restrict_lean_active_universe(active, settings, dt, universe_membership)
    active = active[["ts_code", "name", "list_date", "delist_date", "market", "exchange"]]
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
    frame = _merge_pit_fundamentals(frame, settings, trade_date, pit_fundamentals)

    suspended_codes = (
        set(suspend.loc[suspend.get("suspend_type", pd.Series(dtype=str)) == "S", "ts_code"])
        if not suspend.empty
        else set()
    )
    st_codes = set(st["ts_code"]) if not st.empty else set()
    frame["known_suspended"] = frame["ts_code"].isin(suspended_codes).astype(float)
    # Historical suspend_d records can conflict with an observed same-day trade.
    # The observed close is authoritative for whether the Qlib bar is paused;
    # retain known_suspended separately for audit and diagnostics.
    frame["paused"] = frame["close"].isna().astype(float)
    stock_st_manifest = raw.read_manifest("stock_st", trade_date)
    st_known = stock_st_manifest.get("status") in {"success", "empty"}
    frame["is_st"] = frame["ts_code"].isin(st_codes).astype(float) if st_known else np.nan
    frame["symbol"] = frame["ts_code"].map(ts_to_qlib)
    frame["date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d")

    # A broad, source-confirmed suspension event can legitimately reduce
    # same-day trading coverage below the normal 50% threshold. This is only
    # accepted when suspend_d marks at least 40% of the active universe.
    report = validate_curated(
        frame,
        expected_trade_date=trade_date,
        min_traded_coverage=0.40 if float(frame["known_suspended"].mean()) >= 0.40 else 0.50,
    )
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
            for name in (
                "daily",
                "adj_factor",
                "daily_basic",
                "moneyflow",
                "stk_limit",
                "suspend_d",
                "stock_st",
            )
        },
        "pit_fundamentals": {
            "path": str(pit_fundamentals_path(settings)),
            "sha256": (
                sha256_file(pit_fundamentals_path(settings))
                if pit_fundamentals_path(settings).exists()
                else None
            ),
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
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
    master_path = settings.paths.metadata / "stock_master.parquet"
    if not master_path.is_file():
        raise FileNotFoundError(f"stock master not found: {master_path}")
    master = pd.read_parquet(master_path)
    fundamentals_path = pit_fundamentals_path(settings)
    pit_fundamentals = pd.read_parquet(fundamentals_path) if fundamentals_path.is_file() else None
    configured = configured_universe(settings)
    universe_membership = (
        pd.read_parquet(configured[2])
        if configured is not None and not settings.uses_tushare_source() and configured[2].is_file()
        else None
    )
    start = _normalize_trade_date(start_date) if start_date else None
    end = _normalize_trade_date(end_date) if end_date else None
    for trade_date in raw.list_dates("daily"):
        if start and trade_date < start:
            continue
        if end and trade_date > end:
            continue
        build_curated_day(
            settings,
            trade_date,
            master=master,
            pit_fundamentals=pit_fundamentals,
            universe_membership=universe_membership,
        )


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
    return cast(np.ndarray, np.asarray(np.maximum(date_idx - list_idx, 0), dtype=float))


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

    market_fields = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "money",
        "vwap",
        "factor",
        "change",
        "up_limit",
        "down_limit",
    ]
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
        *PIT_FIELDS,
    ]
    for col in keep:
        if col not in df:
            df[col] = np.nan
    return df[keep], base_adj_close


def _curated_glob(settings: Settings) -> str:
    return (settings.paths.curated / "trade_date=*" / "data.parquet").resolve().as_posix()


def _benchmark_staging_frame(settings: Settings, calendar: pd.DatetimeIndex) -> pd.DataFrame:
    path = settings.paths.metadata / "benchmarks" / "SH000300.parquet"
    if not path.exists():
        raise FileNotFoundError(f"benchmark data is required for Qlib export: {path}")
    frame = pd.read_parquet(path).copy()
    required = {"trade_date", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"benchmark file missing columns: {sorted(missing)}")
    frame["date"] = pd.to_datetime(frame["trade_date"], errors="raise")
    frame = frame.loc[frame["date"].isin(calendar)].sort_values("date").drop_duplicates("date", keep="last")
    close = pd.to_numeric(frame["close"], errors="coerce")
    if close.isna().any() or (close <= 0).any():
        raise ValueError("benchmark close must be present and positive")
    for column in ("open", "high", "low"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce") if column in frame else close
        frame[column] = frame[column].fillna(close)
    for raw_column, output_column, scale in (("vol", "volume", 100.0), ("amount", "money", 1000.0)):
        frame[output_column] = (
            pd.to_numeric(frame[raw_column], errors="coerce") * scale if raw_column in frame else np.nan
        )
    frame["vwap"] = frame["money"] / frame["volume"]
    frame["factor"] = 1.0
    frame["change"] = (
        pd.to_numeric(frame["pct_chg"], errors="coerce") / 100.0 if "pct_chg" in frame else np.nan
    )
    frame["paused"] = 0.0
    frame["symbol"] = "SH000300"
    for column in settings.data["qlib"]["include_fields"]:
        if column not in frame:
            frame[column] = np.nan
    return frame[["date", "symbol", *settings.data["qlib"]["include_fields"]]]


def _remove_staging_tree(path: Path) -> None:
    for attempt in range(5):
        try:
            shutil.rmtree(path)
            return
        except OSError:
            if attempt == 4:
                raise
            time.sleep(1)


def _write_stage_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".parquet.tmp")
    frame.to_parquet(temporary, index=False)
    _atomic_replace(temporary, path)


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
    con.execute("SET threads=1")
    con.execute("SET preserve_insertion_order=false")
    con.execute("SET memory_limit='8GB'")
    spill_dir = stage / ".duckdb_spill"
    spill_dir.mkdir(parents=True, exist_ok=True)
    spill_path = str(spill_dir).replace("\\", "/")
    con.execute(f"SET temp_directory='{spill_path}'")
    glob = _curated_glob(settings)
    raw_by_symbol = stage / ".curated_by_symbol"
    source_sql = glob.replace("'", "''")
    target_sql = str(raw_by_symbol).replace("\\", "/").replace("'", "''")
    con.execute(
        f"COPY (SELECT * FROM read_parquet('{source_sql}')) TO '{target_sql}' (FORMAT PARQUET, PARTITION_BY (symbol))"
    )
    symbols = sorted(path.name.split("=", 1)[1] for path in raw_by_symbol.glob("symbol=*"))

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
        partition = raw_by_symbol / f"symbol={symbol}"
        files = sorted(partition.glob("*.parquet"))
        if not files:
            raise FileNotFoundError(f"curated symbol partition missing: {partition}")
        raw_df = pd.read_parquet(files).sort_values("date")
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
        _write_stage_parquet(norm, target)
        bases[symbol] = base
        if i % 200 == 0 or i == len(symbols):
            logger.info("Staging full: {}/{}", i, len(symbols))
    con.close()
    shutil.rmtree(raw_by_symbol)
    shutil.rmtree(spill_dir, ignore_errors=True)
    _write_stage_parquet(_benchmark_staging_frame(settings, calendar), stage / "SH000300.parquet")
    pd.DataFrame([{"symbol": k, "base_adj_close": v} for k, v in sorted(bases.items())]).to_parquet(
        base_path, index=False
    )
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
    bases = (
        pd.read_parquet(base_path).set_index("symbol")["base_adj_close"].to_dict()
        if base_path.exists()
        else {}
    )
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
        _write_stage_parquet(norm, stage / f"{symbol}.parquet")
        if symbol not in bases:
            new_bases.append({"symbol": symbol, "base_adj_close": base})
    benchmark = _benchmark_staging_frame(settings, calendar)
    selected_dates = pd.to_datetime(normalized_dates, format="%Y%m%d")
    benchmark = benchmark.loc[benchmark["date"].isin(selected_dates)]
    if benchmark.empty:
        raise ValueError(f"benchmark does not cover incremental dates: {normalized_dates}")
    _write_stage_parquet(benchmark, stage / "SH000300.parquet")
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


def export_symbol_repair_staging(settings: Settings, symbols: list[str], force: bool = True) -> Path:
    """Build complete normalized histories for symbols with revised source rows."""

    import duckdb

    normalized_symbols = sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})
    if not normalized_symbols:
        raise ValueError("repair staging requires at least one symbol")
    stage = settings.paths.staging_repair
    if stage.exists() and force:
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=True)
    calendar = _load_open_calendar(settings)
    base_path = settings.paths.metadata / "normalization_base.parquet"
    bases = (
        pd.read_parquet(base_path).set_index("symbol")["base_adj_close"].to_dict()
        if base_path.exists()
        else {}
    )
    placeholders = ",".join("?" for _ in normalized_symbols)
    con = duckdb.connect()
    try:
        frame = con.execute(
            f"SELECT * FROM read_parquet(?) WHERE symbol IN ({placeholders}) ORDER BY symbol,date",
            [_curated_glob(settings), *normalized_symbols],
        ).df()
    finally:
        con.close()
    found = set(frame["symbol"].astype(str)) if not frame.empty else set()
    missing = sorted(set(normalized_symbols) - found)
    if missing:
        raise FileNotFoundError(f"curated histories missing repair symbols: {missing[:10]}")
    for symbol, group in frame.groupby("symbol", sort=True):
        norm, base = normalize_symbol(group, calendar, bases.get(str(symbol)))
        report = validate_normalized(norm, str(symbol))
        assert_quality(report)
        _write_stage_parquet(norm, stage / f"{symbol}.parquet")
        if symbol not in bases:
            bases[str(symbol)] = base
    pd.DataFrame(
        [{"symbol": key, "base_adj_close": value} for key, value in sorted(bases.items())]
    ).to_parquet(base_path, index=False)
    manifest = _write_staging_manifest(stage, "repair")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["symbols"] = normalized_symbols
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return stage
