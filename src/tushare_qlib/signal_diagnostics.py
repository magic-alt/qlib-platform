from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _series(value: pd.Series | pd.DataFrame, name: str) -> pd.Series:
    if isinstance(value, pd.DataFrame):
        if value.shape[1] != 1:
            raise ValueError(f"{name} must contain exactly one column")
        value = value.iloc[:, 0]
    result = pd.to_numeric(value, errors="coerce")
    if not isinstance(result.index, pd.MultiIndex) or "datetime" not in result.index.names:
        raise ValueError(f"{name} must use a MultiIndex containing datetime")
    return result


def build_signal_diagnostics(
    predictions: pd.Series | pd.DataFrame,
    labels: pd.Series | pd.DataFrame,
    *,
    rolling_window: int = 63,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build portable daily, rolling and monthly signal-quality diagnostics."""

    if rolling_window < 2:
        raise ValueError("rolling_window must be at least 2")
    paired = pd.concat(
        [_series(predictions, "predictions").rename("score"), _series(labels, "labels").rename("label")],
        axis=1,
    )
    paired = paired.dropna().sort_index()
    grouped = paired.groupby(level="datetime", sort=True)

    def top_bottom(frame: pd.DataFrame) -> float:
        size = max(1, len(frame) // 5)
        ranked = frame.sort_values("score", ascending=False)
        return float(ranked.head(size)["label"].mean() - ranked.tail(size)["label"].mean())

    daily = pd.DataFrame(
        {
            "ic": grouped.apply(lambda frame: frame["score"].corr(frame["label"])),
            "rank_ic": grouped.apply(lambda frame: frame["score"].corr(frame["label"], method="spearman")),
            "top_bottom_spread": grouped.apply(top_bottom),
            "cross_section_size": grouped.size(),
        }
    ).dropna(subset=["ic", "rank_ic"])
    daily.index = pd.to_datetime(daily.index).normalize()
    daily.index.name = "trade_date"
    # Pair each session with its immediate predecessor only on common names.
    autocorrelation: list[float] = []
    dates = list(daily.index)
    for index, date in enumerate(dates):
        if index == 0:
            autocorrelation.append(np.nan)
            continue
        current = grouped.get_group(date)["score"].droplevel("datetime")
        prior = grouped.get_group(dates[index - 1])["score"].droplevel("datetime")
        autocorrelation.append(float(current.corr(prior, method="spearman")))
    daily["prediction_autocorrelation"] = autocorrelation
    daily["rolling_ic_63d"] = daily["ic"].rolling(rolling_window, min_periods=rolling_window).mean()
    daily["rolling_rank_ic_63d"] = daily["rank_ic"].rolling(rolling_window, min_periods=rolling_window).mean()
    daily["period"] = daily.index.to_period("M").astype(str)
    monthly = daily.groupby("period", sort=True).agg(
        sessions=("ic", "size"),
        ic=("ic", "mean"),
        rank_ic=("rank_ic", "mean"),
        top_bottom_spread=("top_bottom_spread", "mean"),
        prediction_autocorrelation=("prediction_autocorrelation", "mean"),
    )

    def ratio(values: pd.Series) -> float:
        std = float(values.std(ddof=1))
        return float(values.mean() / std) if np.isfinite(std) and std > 0 else float("nan")

    summary: dict[str, Any] = {
        "schemaVersion": "1.0",
        "rollingWindow": rolling_window,
        "observations": int(len(daily)),
        "ic": float(daily["ic"].mean()) if len(daily) else float("nan"),
        "icir": ratio(daily["ic"]),
        "rankIC": float(daily["rank_ic"].mean()) if len(daily) else float("nan"),
        "rankICIR": ratio(daily["rank_ic"]),
        "topBottomSpread": float(daily["top_bottom_spread"].mean()) if len(daily) else float("nan"),
        "predictionAutocorrelation": float(daily["prediction_autocorrelation"].mean()),
        "monthly": monthly.reset_index().to_dict(orient="records"),
    }
    return daily.reset_index(), summary
