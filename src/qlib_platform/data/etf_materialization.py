from __future__ import annotations

import numpy as np
import pandas as pd

ETF_QLIB_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "money",
    "vwap",
    "factor",
)


def materialize_etf_qlib_fields(
    daily: pd.DataFrame,
    adjustment: pd.DataFrame,
) -> pd.DataFrame:
    """Build the ETF Qlib price/volume contract from canonical daily + fund adjustment data.

    The normalization intentionally mirrors the frozen stock price/volume convention:
    factor = provider_adj_factor / first(close * provider_adj_factor), adjusted OHLC = raw OHLC * factor,
    adjusted volume = raw shares / factor, and money remains raw traded notional.
    """

    daily_required = {
        "instrument",
        "trading_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "turnover",
    }
    adjustment_required = {"instrument", "trading_date", "adjustment_factor"}
    missing_daily = sorted(daily_required - set(daily.columns))
    missing_adjustment = sorted(adjustment_required - set(adjustment.columns))
    if missing_daily:
        raise ValueError(f"ETF daily materialization is missing columns: {missing_daily}")
    if missing_adjustment:
        raise ValueError(f"ETF adjustment materialization is missing columns: {missing_adjustment}")

    daily_frame = daily.copy()
    adjustment_frame = adjustment.copy()
    daily_frame["trading_date"] = pd.to_datetime(daily_frame["trading_date"], errors="raise").dt.normalize()
    adjustment_frame["trading_date"] = pd.to_datetime(
        adjustment_frame["trading_date"], errors="raise"
    ).dt.normalize()
    keys = ["instrument", "trading_date"]
    if daily_frame.duplicated(keys).any():
        raise ValueError("ETF daily materialization contains duplicate instrument/date keys")
    if adjustment_frame.duplicated(keys).any():
        raise ValueError("ETF adjustment materialization contains duplicate instrument/date keys")

    merged = daily_frame.merge(
        adjustment_frame[keys + ["adjustment_factor"]],
        on=keys,
        how="left",
        validate="one_to_one",
    )
    if merged["adjustment_factor"].isna().any():
        raise ValueError("ETF adjustment factor is missing for one or more daily observations")

    for column in ("open", "high", "low", "close", "volume", "turnover", "adjustment_factor"):
        merged[column] = pd.to_numeric(merged[column], errors="coerce")
    numeric = merged[["open", "high", "low", "close", "volume", "turnover", "adjustment_factor"]]
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise ValueError("ETF materialization inputs must be finite")
    if (merged["close"] <= 0).any() or (merged["adjustment_factor"] <= 0).any():
        raise ValueError("ETF close and adjustment factor must be positive")
    if (merged["volume"] < 0).any() or (merged["turnover"] < 0).any():
        raise ValueError("ETF volume and turnover must not be negative")

    merged = merged.sort_values(keys, kind="stable").reset_index(drop=True)
    outputs: list[pd.DataFrame] = []
    for _, group in merged.groupby("instrument", sort=True):
        first = group.iloc[0]
        base_adj_close = float(first["close"] * first["adjustment_factor"])
        if not np.isfinite(base_adj_close) or base_adj_close <= 0:
            raise ValueError("ETF base adjusted close must be positive")
        factor = group["adjustment_factor"] / base_adj_close
        if (factor <= 0).any():
            raise ValueError("ETF normalized factor must remain positive")
        output = group[["instrument", "trading_date"]].copy()
        for column in ("open", "high", "low", "close"):
            output[column] = group[column] * factor
        output["volume"] = group["volume"] / factor
        output["money"] = group["turnover"]
        output["vwap"] = np.where(
            output["volume"] > 0,
            output["money"] / output["volume"],
            np.nan,
        )
        output["factor"] = factor
        outputs.append(output)

    if not outputs:
        raise ValueError("ETF materialization requires at least one daily observation")
    return pd.concat(outputs, ignore_index=True).sort_values(keys, kind="stable").reset_index(drop=True)
