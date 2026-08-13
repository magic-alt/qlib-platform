from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

import pandas as pd

from .settings import Settings
from .store import PartitionStore


@dataclass(frozen=True)
class LabelTiming:
    """Canonical relationship between a signal date and its forward-return label."""

    horizon_days: int
    signal_lag_days: int

    @property
    def lookahead_days(self) -> int:
        return self.horizon_days + self.signal_lag_days

    def to_manifest(self) -> dict[str, int]:
        return {**asdict(self), "lookahead_days": self.lookahead_days}


def label_timing_from_settings(settings: Settings) -> LabelTiming:
    research = settings.data.get("research", {})
    strategy = settings.data.get("strategy", {})
    topk = strategy.get("topk_dropout", {}) if isinstance(strategy, Mapping) else {}
    default_horizon = int(topk.get("hold_thresh", 1)) if isinstance(topk, Mapping) else 1
    horizon = (
        int(research.get("label_horizon_days", default_horizon))
        if isinstance(research, Mapping)
        else default_horizon
    )
    lag = int(research.get("signal_lag_days", 1)) if isinstance(research, Mapping) else 1
    if horizon < 1:
        raise ValueError("research.label_horizon_days must be at least 1")
    if lag < 1:
        raise ValueError("research.signal_lag_days must be at least 1")
    return LabelTiming(horizon_days=horizon, signal_lag_days=lag)


def effective_label_gap(configured: object, timing: LabelTiming) -> tuple[int, int]:
    requested = timing.lookahead_days if configured is None else int(str(configured))
    if requested < 0:
        raise ValueError("label timing gaps must be non-negative")
    return requested, max(requested, timing.lookahead_days)


def _read_dates(path: Path) -> pd.DatetimeIndex:
    if not path.is_file():
        raise FileNotFoundError(f"required calendar is missing: {path}")
    values = pd.to_datetime(path.read_text(encoding="utf-8").splitlines(), errors="coerce")
    return pd.DatetimeIndex(sorted(value for value in values if pd.notna(value))).normalize()


def shared_research_calendar(settings: Settings) -> pd.DatetimeIndex:
    from .dataset_resolver import pin_dataset

    settings, _ = pin_dataset(settings)
    """Return dates present in raw partitions, Qlib data and the official open calendar."""

    raw = pd.to_datetime(
        PartitionStore(settings.paths.raw).list_dates("daily"), format="%Y%m%d", errors="coerce"
    )
    raw_dates = pd.DatetimeIndex(sorted(value for value in raw if pd.notna(value))).normalize()
    qlib_dates = _read_dates(settings.qlib_data_uri / "calendars" / "day.txt")
    if raw_dates.empty:
        raise ValueError("raw daily store contains no trading dates")
    if qlib_dates.empty:
        raise ValueError("Qlib calendar contains no trading dates")
    official_path = settings.paths.metadata / "trade_calendar.parquet"
    if not official_path.is_file():
        raise FileNotFoundError(f"official trading calendar is required: {official_path}")
    official = pd.read_parquet(official_path)
    required = {"cal_date", "is_open"}
    if not required.issubset(official.columns):
        raise ValueError(f"official calendar missing columns: {sorted(required - set(official.columns))}")
    open_dates = pd.to_datetime(
        official.loc[pd.to_numeric(official["is_open"], errors="coerce") == 1, "cal_date"],
        errors="coerce",
    )
    official_dates = pd.DatetimeIndex(open_dates.dropna().sort_values().unique()).normalize()
    if official_dates.empty:
        raise ValueError("official trading calendar contains no open dates")
    covered_source_end = min(raw_dates.max(), qlib_dates.max())
    if official_dates.max() < covered_source_end:
        raise ValueError(
            "official trading calendar is stale: "
            f"last open date {official_dates.max().date()} precedes raw/Qlib data "
            f"through {covered_source_end.date()}"
        )
    dates = raw_dates.intersection(qlib_dates).intersection(official_dates).sort_values()
    if dates.empty:
        raise ValueError("raw, Qlib and official trading calendars have no shared open dates")
    return dates
