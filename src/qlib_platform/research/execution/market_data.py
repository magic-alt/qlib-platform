from __future__ import annotations

import numpy as np
import pandas as pd

from qlib_platform.research.execution.types import ExecutionBenchmarks, ParentOrder

_REQUIRED_COLUMNS = {"timestamp", "instrument", "close", "volume"}
_OPTIONAL_DEFAULTS: dict[str, object] = {
    "bid": np.nan,
    "ask": np.nan,
    "spread_bps": np.nan,
    "queue_ahead_quantity": 0.0,
    "market_open": True,
    "rejected": False,
}


def normalize_intraday_bars(frame: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(_REQUIRED_COLUMNS.difference(frame.columns))
    if missing:
        raise ValueError(f"intraday bars missing required columns: {missing}")
    if frame.empty:
        raise ValueError("intraday bars must be non-empty")

    normalized = frame.copy()
    normalized["timestamp"] = pd.to_datetime(normalized["timestamp"], errors="coerce")
    if bool(normalized["timestamp"].isna().any()):
        raise ValueError("intraday bars contain invalid timestamps")

    normalized["instrument"] = normalized["instrument"].astype(str)
    if bool(normalized["instrument"].str.strip().eq("").any()):
        raise ValueError("intraday bars contain blank instruments")

    for column in ("close", "volume"):
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    if bool(normalized[["close", "volume"]].isna().any().any()):
        raise ValueError("intraday close and volume must be numeric")
    if bool((normalized["close"] <= 0).any()):
        raise ValueError("intraday close must be positive")
    if bool((normalized["volume"] < 0).any()):
        raise ValueError("intraday volume must be non-negative")

    for column, default in _OPTIONAL_DEFAULTS.items():
        if column not in normalized.columns:
            normalized[column] = default

    for column in ("bid", "ask", "spread_bps", "queue_ahead_quantity"):
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    if bool((normalized["queue_ahead_quantity"].fillna(0.0) < 0).any()):
        raise ValueError("queue_ahead_quantity must be non-negative")
    normalized["queue_ahead_quantity"] = normalized["queue_ahead_quantity"].fillna(0.0)

    bid_present = normalized["bid"].notna()
    ask_present = normalized["ask"].notna()
    if bool((bid_present ^ ask_present).any()):
        raise ValueError("bid and ask must either both be present or both be missing")
    quoted = bid_present & ask_present
    if bool(((normalized.loc[quoted, "bid"] <= 0) | (normalized.loc[quoted, "ask"] <= 0)).any()):
        raise ValueError("bid and ask must be positive")
    if bool((normalized.loc[quoted, "ask"] < normalized.loc[quoted, "bid"]).any()):
        raise ValueError("ask must not be below bid")

    normalized["mid_price"] = normalized["close"].astype(float)
    normalized.loc[quoted, "mid_price"] = (
        normalized.loc[quoted, "bid"] + normalized.loc[quoted, "ask"]
    ) / 2.0
    derived_spread = pd.Series(np.nan, index=normalized.index, dtype=float)
    derived_spread.loc[quoted] = (
        (normalized.loc[quoted, "ask"] - normalized.loc[quoted, "bid"])
        / normalized.loc[quoted, "mid_price"]
        * 10_000.0
    )
    normalized["spread_bps"] = normalized["spread_bps"].where(
        normalized["spread_bps"].notna(), derived_spread
    )
    if bool((normalized["spread_bps"].dropna() < 0).any()):
        raise ValueError("spread_bps must be non-negative")

    normalized["market_open"] = normalized["market_open"].astype(bool)
    normalized["rejected"] = normalized["rejected"].astype(bool)

    if bool(normalized.duplicated(subset=["timestamp", "instrument"]).any()):
        raise ValueError("intraday bars must be unique by timestamp and instrument")
    return normalized.sort_values(["timestamp", "instrument"], kind="stable").reset_index(drop=True)


def order_window(bars: pd.DataFrame, order: ParentOrder) -> pd.DataFrame:
    normalized = normalize_intraday_bars(bars)
    start = pd.Timestamp(order.start_time)
    end = pd.Timestamp(order.end_time)
    window = normalized.loc[
        (normalized["instrument"] == order.instrument)
        & (normalized["timestamp"] >= start)
        & (normalized["timestamp"] <= end)
    ].copy()
    if window.empty:
        raise ValueError("no intraday bars fall inside the parent-order window")
    return window.reset_index(drop=True)


def execution_benchmarks(bars: pd.DataFrame, order: ParentOrder) -> ExecutionBenchmarks:
    window = order_window(bars, order)
    total_volume = float(window["volume"].sum())
    if total_volume <= 0:
        raise ValueError("positive market volume is required to compute VWAP")
    arrival_price = float(window.iloc[0]["mid_price"])
    end_price = float(window.iloc[-1]["mid_price"])
    twap = float(window["mid_price"].mean())
    vwap = float((window["mid_price"] * window["volume"]).sum() / total_volume)
    return ExecutionBenchmarks(
        arrival_price=arrival_price,
        vwap=vwap,
        twap=twap,
        end_price=end_price,
        total_volume=total_volume,
    )
