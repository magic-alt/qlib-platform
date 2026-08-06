from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class RuleStrategyConfig:
    momentum_20_weight: float = 0.30
    momentum_60_weight: float = 0.25
    quality_weight: float = 0.20
    low_vol_weight: float = 0.15
    liquidity_weight: float = 0.10
    reversal_5_penalty: float = 0.10
    min_history: int = 80


def _cs_zscore(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    median = values.median()
    mad = (values - median).abs().median()
    scale = 1.4826 * mad
    if not np.isfinite(scale) or scale <= 1e-12:
        scale = values.std(ddof=0)
    if not np.isfinite(scale) or scale <= 1e-12:
        return pd.Series(0.0, index=series.index)
    return ((values - median) / scale).clip(-5, 5).fillna(0.0)


def momentum_quality_lowvol_signals(
    history: pd.DataFrame, config: RuleStrategyConfig | None = None
) -> pd.DataFrame:
    """Create a transparent cross-sectional baseline from adjusted daily bars.

    Required columns: date, instrument/symbol, close, money. Optional: pe_ttm, pb, is_st, paused.
    The result is intended as a benchmark/diversifier, not as a claim of profitability.
    """

    config = config or RuleStrategyConfig()
    instrument_col = "instrument" if "instrument" in history.columns else "symbol"
    required = {"date", instrument_col, "close", "money"}
    missing = required - set(history.columns)
    if missing:
        raise ValueError(f"history missing columns: {sorted(missing)}")
    frame = history.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    frame = frame.sort_values([instrument_col, "date"])
    grouped = frame.groupby(instrument_col, group_keys=False)
    frame["mom20"] = grouped["close"].pct_change(20)
    frame["mom60"] = grouped["close"].pct_change(60)
    frame["rev5"] = grouped["close"].pct_change(5)
    daily_return = grouped["close"].pct_change()
    frame["vol20"] = daily_return.groupby(frame[instrument_col]).rolling(20).std().reset_index(level=0, drop=True)
    frame["money20"] = grouped["money"].rolling(20).mean().reset_index(level=0, drop=True)
    frame["history_count"] = grouped.cumcount() + 1
    latest_date = frame["date"].max()
    latest = frame.loc[frame["date"] == latest_date].copy()
    latest = latest.loc[latest["history_count"] >= config.min_history]
    if "paused" in latest:
        latest = latest.loc[pd.to_numeric(latest["paused"], errors="coerce").fillna(1) < 0.5]
    if "is_st" in latest:
        latest = latest.loc[pd.to_numeric(latest["is_st"], errors="coerce").fillna(1) < 0.5]

    if {"pe_ttm", "pb"}.issubset(latest.columns):
        pe = pd.to_numeric(latest["pe_ttm"], errors="coerce")
        pb = pd.to_numeric(latest["pb"], errors="coerce")
        quality = -_cs_zscore(pe.where(pe > 0)) - _cs_zscore(pb.where(pb > 0))
    else:
        quality = pd.Series(0.0, index=latest.index)
    score = (
        config.momentum_20_weight * _cs_zscore(latest["mom20"])
        + config.momentum_60_weight * _cs_zscore(latest["mom60"])
        + config.quality_weight * quality
        - config.low_vol_weight * _cs_zscore(latest["vol20"])
        + config.liquidity_weight * _cs_zscore(np.log1p(latest["money20"]))
        - config.reversal_5_penalty * _cs_zscore(latest["rev5"])
    )
    output = pd.DataFrame(
        {
            "signal_date": latest_date,
            "instrument": latest[instrument_col].astype(str),
            "score": score,
            "volatility": latest["vol20"],
            "adv20_amount": latest["money20"],
            "strategy": "momentum_quality_lowvol_v1",
        }
    )
    return output.sort_values(["score", "instrument"], ascending=[False, True]).reset_index(drop=True)


def blend_model_scores(
    predictions: pd.DataFrame,
    weights: Mapping[str, float],
    *,
    instrument_col: str = "instrument",
) -> pd.DataFrame:
    missing = {instrument_col, *weights} - set(predictions.columns)
    if missing:
        raise ValueError(f"predictions missing columns: {sorted(missing)}")
    if not weights or sum(abs(float(v)) for v in weights.values()) <= 0:
        raise ValueError("at least one non-zero model weight is required")
    frame = predictions.copy()
    score = pd.Series(0.0, index=frame.index)
    norm = sum(abs(float(v)) for v in weights.values())
    for column, weight in weights.items():
        ranked = pd.to_numeric(frame[column], errors="coerce").rank(pct=True, method="average")
        score += float(weight) / norm * ranked.fillna(0.5)
    result = frame[[instrument_col]].rename(columns={instrument_col: "instrument"})
    result["score"] = score
    result["strategy"] = "rank_ensemble"
    return result.sort_values(["score", "instrument"], ascending=[False, True]).reset_index(drop=True)
