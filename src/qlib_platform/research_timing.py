from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
from typing import Mapping

import pandas as pd

from .settings import Settings
from .store import PartitionStore


@dataclass(frozen=True)
class LabelSpec:
    """Canonical relationship between a signal date and its forward-return label."""

    horizon_days: int
    signal_lag_days: int
    price_field: str = "close"

    @property
    def lookahead_days(self) -> int:
        return self.horizon_days + self.signal_lag_days

    @property
    def spec_id(self) -> str:
        suffix = "" if self.price_field == "close" else f"_{self.price_field}"
        return f"return_{self.horizon_days}d_t{self.signal_lag_days}{suffix}_v1"

    @property
    def expression(self) -> str:
        return f"Ref(${self.price_field}, -{self.lookahead_days})/Ref(${self.price_field}, -1) - 1"

    def qlib_config(self) -> tuple[list[str], list[str]]:
        return [self.expression], ["LABEL0"]

    def to_manifest(self) -> dict[str, object]:
        return {
            **asdict(self),
            "label_spec_id": self.spec_id,
            "lookahead_days": self.lookahead_days,
            "expression": self.expression,
        }


# Compatibility name for callers that only consume timing fields.
LabelTiming = LabelSpec


def label_spec_from_settings(settings: Settings) -> LabelSpec:
    research = settings.data.get("research", {})
    strategy = settings.data.get("strategy", {})
    topk = strategy.get("topk_dropout", {}) if isinstance(strategy, Mapping) else {}
    default_horizon = int(topk.get("hold_thresh", 1)) if isinstance(topk, Mapping) else 1
    experiment = settings.data.get("experiment", {})
    label_config = experiment.get("label", {}) if isinstance(experiment, Mapping) else {}
    configured_spec = str(label_config.get("spec") or "") if isinstance(label_config, Mapping) else ""
    match = (
        re.fullmatch(r"return_(\d+)d_t(\d+)(?:_(open|close))?_v1", configured_spec)
        if configured_spec
        else None
    )
    if configured_spec and match is None:
        raise ValueError(f"unknown label spec: {configured_spec}")
    horizon = (
        int(match.group(1))
        if match
        else int(research.get("label_horizon_days", default_horizon))
        if isinstance(research, Mapping)
        else default_horizon
    )
    lag = (
        int(match.group(2))
        if match
        else int(research.get("signal_lag_days", 1))
        if isinstance(research, Mapping)
        else 1
    )
    if match and isinstance(research, Mapping):
        if "label_horizon_days" in research and int(research["label_horizon_days"]) != horizon:
            raise ValueError("experiment label conflicts with research.label_horizon_days")
        if "signal_lag_days" in research and int(research["signal_lag_days"]) != lag:
            raise ValueError("experiment label conflicts with research.signal_lag_days")
    if horizon < 1:
        raise ValueError("research.label_horizon_days must be at least 1")
    if lag < 1:
        raise ValueError("research.signal_lag_days must be at least 1")
    price_field = str(
        match.group(3)
        if match and match.group(3)
        else label_config.get("price_field", research.get("label_price_field", "close"))
    ).lower()
    if price_field not in {"close", "open"}:
        raise ValueError("label price_field must be close or open")
    return LabelSpec(horizon_days=horizon, signal_lag_days=lag, price_field=price_field)


def label_timing_from_settings(settings: Settings) -> LabelSpec:
    return label_spec_from_settings(settings)


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
    """Return governed open dates that are also present in the pinned Qlib dataset."""

    qlib_dates = _read_dates(settings.qlib_data_uri / "calendars" / "day.txt")
    if qlib_dates.empty:
        raise ValueError("Qlib calendar contains no trading dates")

    if settings.uses_platform_release():
        # Production research is owned by platform's immutable DataRelease.  The
        # legacy raw store is intentionally absent in this mode and must not be
        # consulted as an undeclared second data source.
        from .platform_release import load_platform_release

        release = load_platform_release(settings)
        official = pd.concat(
            (pd.read_parquet(path) for path in release.files("trading_calendar")),
            ignore_index=True,
        )
        required = {"cal_date", "is_open"}
        if not required.issubset(official.columns):
            raise ValueError(
                f"DataRelease trading calendar missing columns: {sorted(required - set(official.columns))}"
            )
        open_dates = pd.to_datetime(
            official.loc[pd.to_numeric(official["is_open"], errors="coerce") == 1, "cal_date"],
            errors="coerce",
        )
        official_dates = pd.DatetimeIndex(open_dates.dropna().sort_values().unique()).normalize()
        coverage_start = pd.Timestamp(str(release.coverage.get("start"))).normalize()
        coverage_end = pd.Timestamp(str(release.coverage.get("end"))).normalize()
        official_dates = official_dates[(official_dates >= coverage_start) & (official_dates <= coverage_end)]
        dates = qlib_dates.intersection(official_dates).sort_values()
        if dates.empty:
            raise ValueError("DataRelease and Qlib calendars have no shared open dates")
        if dates.min() != coverage_start or dates.max() != coverage_end:
            raise ValueError(
                "pinned Qlib calendar does not cover the DataRelease research interval: "
                f"expected {coverage_start.date()}..{coverage_end.date()}, "
                f"found {dates.min().date()}..{dates.max().date()}"
            )
        return dates

    # The repository-owned TuShare path remains available only for the explicit
    # development profile, where raw, Qlib and curated official calendars agree.

    raw = pd.to_datetime(
        PartitionStore(settings.paths.raw).list_dates("daily"), format="%Y%m%d", errors="coerce"
    )
    raw_dates = pd.DatetimeIndex(sorted(value for value in raw if pd.notna(value))).normalize()
    if raw_dates.empty:
        raise ValueError("raw daily store contains no trading dates")
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
