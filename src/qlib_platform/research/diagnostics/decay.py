from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd

from qlib_platform.research.diagnostics.features import newey_west_t


def _age_labels(upper_bounds: Sequence[int]) -> tuple[list[float], list[str]]:
    upper = [int(value) for value in upper_bounds]
    if not upper or upper != sorted(set(upper)) or upper[0] < 0:
        raise ValueError("age bucket upper bounds must be non-negative, unique, and increasing")
    edges: list[float] = [-1.0, *[float(value) for value in upper], float("inf")]
    labels: list[str] = []
    lower = 0
    for value in upper:
        labels.append(f"{lower}-{value}")
        lower = value + 1
    labels.append(f"{lower}+")
    return edges, labels


def attach_model_age(
    daily_metrics: pd.DataFrame,
    fold_calendar: Sequence[Mapping[str, object]],
) -> pd.DataFrame:
    required = {"date", "model", "rank_ic"}
    if missing := required - set(daily_metrics):
        raise ValueError(f"daily model metrics are missing columns: {sorted(missing)}")
    frame = daily_metrics.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
    frame["fold_id"] = pd.Series(pd.NA, index=frame.index, dtype="string")
    frame["train_end"] = pd.NaT
    frame["test_start"] = pd.NaT
    for raw in fold_calendar:
        fold_id = str(raw.get("foldId") or "").strip()
        start = pd.Timestamp(raw.get("start")).normalize()
        end = pd.Timestamp(raw.get("end")).normalize()
        train_end_raw = raw.get("trainEnd")
        if not fold_id or start > end:
            raise ValueError("fold calendar contains an invalid identity or window")
        mask = frame["date"].between(start, end)
        if frame.loc[mask, "fold_id"].notna().any():
            raise ValueError("fold calendar contains overlapping test windows")
        frame.loc[mask, "fold_id"] = fold_id
        frame.loc[mask, "test_start"] = start
        if train_end_raw is not None:
            frame.loc[mask, "train_end"] = pd.Timestamp(train_end_raw).normalize()
    if frame["fold_id"].isna().any():
        missing_dates = sorted(frame.loc[frame["fold_id"].isna(), "date"].dt.date.astype(str).unique())[:5]
        raise ValueError(f"daily metrics dates are outside the frozen fold calendar: {missing_dates}")
    frame = frame.sort_values(["model", "fold_id", "date"], kind="stable")
    frame["model_age_sessions"] = frame.groupby(["model", "fold_id"], sort=False).cumcount()
    frame["model_age_calendar_days"] = (
        frame["date"] - pd.to_datetime(frame["train_end"], errors="coerce")
    ).dt.days
    return frame.reset_index(drop=True)


def derive_model_age_decay(
    daily_metrics: pd.DataFrame,
    fold_calendar: Sequence[Mapping[str, object]],
    *,
    age_bucket_upper_sessions: Sequence[int],
    hac_lag: int,
) -> pd.DataFrame:
    aged = attach_model_age(daily_metrics, fold_calendar)
    edges, labels = _age_labels(age_bucket_upper_sessions)
    aged["age_bucket"] = pd.cut(
        aged["model_age_sessions"], bins=edges, labels=labels, include_lowest=True, right=True
    ).astype("string")
    rows: list[dict[str, object]] = []
    for (model, age_bucket), block in aged.groupby(["model", "age_bucket"], sort=True):
        rank_ic = pd.to_numeric(block["rank_ic"], errors="coerce")
        spread = pd.to_numeric(
            block.get("topk_spread", pd.Series(float("nan"), index=block.index)), errors="coerce"
        )
        turnover = pd.to_numeric(
            block.get("turnover", pd.Series(float("nan"), index=block.index)), errors="coerce"
        )
        standard_deviation = float(rank_ic.std(ddof=1))
        rows.append(
            {
                "model": model,
                "age_bucket": str(age_bucket),
                "minimum_age_sessions": int(block["model_age_sessions"].min()),
                "maximum_age_sessions": int(block["model_age_sessions"].max()),
                "folds": int(block["fold_id"].nunique()),
                "sessions": int(block["date"].nunique()),
                "valid_rank_ic_days": int(rank_ic.notna().sum()),
                "rank_ic_mean": float(rank_ic.mean()),
                "rank_icir": (
                    float(rank_ic.mean() / standard_deviation)
                    if np.isfinite(standard_deviation) and standard_deviation > 0
                    else float("nan")
                ),
                "rank_ic_hac_t": newey_west_t(rank_ic, lag=hac_lag),
                "positive_rank_ic_ratio": float(rank_ic.dropna().gt(0).mean()),
                "topk_spread_mean": float(spread.mean()),
                "turnover_mean": float(turnover.mean()),
                "calendar_age_days_mean": float(
                    pd.to_numeric(block["model_age_calendar_days"], errors="coerce").mean()
                ),
                "ageDefinition": "sessions_since_fold_test_start",
            }
        )
    return (
        pd.DataFrame(rows)
        .sort_values(["model", "minimum_age_sessions"], kind="stable")
        .reset_index(drop=True)
    )
