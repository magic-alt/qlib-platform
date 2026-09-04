from __future__ import annotations

from pathlib import Path

import pandas as pd

from qlib_platform.settings import Settings
from qlib_platform.data.store import PartitionStore
from qlib_platform.data.symbols import qlib_to_ts

PRICE_COLUMNS = ["open", "high", "low", "close", "pre_close"]


def _to_ts_code(symbol: str) -> str:
    value = symbol.strip().upper()
    if len(value) == 9 and value[6] == ".":
        return value
    return qlib_to_ts(value)


def build_kline(
    settings: Settings,
    symbol: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    adjustment: str = "raw",
) -> pd.DataFrame:
    """Build a local raw/qfq/hfq K-line without accessing TuShare."""

    mode = adjustment.strip().lower()
    if mode not in {"raw", "qfq", "hfq"}:
        raise ValueError("adjustment must be raw, qfq or hfq")
    ts_code = _to_ts_code(symbol)
    raw = PartitionStore(settings.paths.raw)
    start = pd.Timestamp(start_date).strftime("%Y%m%d") if start_date else None
    end = pd.Timestamp(end_date).strftime("%Y%m%d") if end_date else None
    frames: list[pd.DataFrame] = []
    factors: list[pd.DataFrame] = []
    for trade_date in raw.list_dates("daily"):
        if start and trade_date < start:
            continue
        if end and trade_date > end:
            continue
        daily = raw.read("daily", trade_date)
        if not daily.empty:
            selected = daily.loc[daily["ts_code"].astype(str).str.upper() == ts_code]
            if not selected.empty:
                frames.append(selected)
        adj = raw.read("adj_factor", trade_date)
        if not adj.empty:
            selected_factor = adj.loc[adj["ts_code"].astype(str).str.upper() == ts_code]
            if not selected_factor.empty:
                factors.append(selected_factor[["ts_code", "trade_date", "adj_factor"]])
    if not frames:
        raise FileNotFoundError(f"no local daily bars for {ts_code}")
    daily_frame = pd.concat(frames, ignore_index=True)
    factor_frame = pd.concat(factors, ignore_index=True) if factors else pd.DataFrame()
    if factor_frame.empty:
        raise FileNotFoundError(f"no local adjustment factors for {ts_code}")
    frame = daily_frame.merge(
        factor_frame,
        on=["ts_code", "trade_date"],
        how="left",
        validate="one_to_one",
    )
    frame["adj_factor"] = pd.to_numeric(frame["adj_factor"], errors="raise")
    if frame["adj_factor"].isna().any() or (frame["adj_factor"] <= 0).any():
        raise ValueError(f"invalid adjustment factors for {ts_code}")
    if mode == "raw":
        multiplier = pd.Series(1.0, index=frame.index)
        anchor_factor = 1.0
    elif mode == "hfq":
        multiplier = frame["adj_factor"]
        anchor_factor = 1.0
    else:
        anchor_factor = float(frame.sort_values("trade_date")["adj_factor"].iloc[-1])
        multiplier = frame["adj_factor"] / anchor_factor
    for column in PRICE_COLUMNS:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce") * multiplier
    frame["adjustment"] = mode
    frame["adjustment_multiplier"] = multiplier
    frame["anchor_factor"] = anchor_factor
    frame["anchor_trade_date"] = str(frame["trade_date"].max())
    return frame.sort_values("trade_date", kind="stable").reset_index(drop=True)


def export_kline(
    settings: Settings,
    symbol: str,
    output: str | Path,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    adjustment: str = "raw",
) -> Path:
    frame = build_kline(
        settings,
        symbol,
        start_date=start_date,
        end_date=end_date,
        adjustment=adjustment,
    )
    target = Path(output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    suffix = target.suffix.lower()
    if suffix == ".csv":
        frame.to_csv(target, index=False)
    elif suffix in {".parquet", ".pq"}:
        frame.to_parquet(target, index=False)
    else:
        raise ValueError("K-line output must use .csv or .parquet")
    return target
