from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Mapping

import pandas as pd

from qlib_platform.settings import Settings
from qlib_platform.data.store import PartitionStore, sha256_file


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


def _versioned_dataset_calendar(
    resolved: object, calendar_path: Path, qlib_dates: pd.DatetimeIndex
) -> pd.DatetimeIndex | None:
    """Return the calendar owned by an immutable v3 DatasetVersion.

    Raw/metadata stores are build-time inputs. Once a DatasetVersion is validated or
    published, research must remain bound to the immutable version instead of silently
    reintroducing mutable source state as a second runtime dependency. The calendar
    checksum is verified directly against the DatasetVersion manifest so this shortcut
    remains fail-closed even when callers bypass the quickstart verifier.
    """

    manifest_raw = getattr(resolved, "manifest_path", None)
    version_id = str(getattr(resolved, "version_id", "") or "").strip()
    if manifest_raw is None or not version_id:
        return None
    manifest_path = Path(manifest_raw)
    if not manifest_path.is_file():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"pinned DatasetVersion manifest is unreadable: {manifest_path}") from exc
    if payload.get("schema_version") != "3.0":
        return None
    manifest_version = str(payload.get("version_id") or "").strip()
    if manifest_version != version_id:
        raise ValueError(
            "pinned DatasetVersion identity does not match its manifest: "
            f"resolved={version_id}, manifest={manifest_version or '<missing>'}"
        )
    status = str(payload.get("status") or "").strip().upper()
    if status not in {"VALIDATED", "PUBLISHED"}:
        raise ValueError(f"pinned DatasetVersion is not research-usable: status={status or '<missing>'}")
    partitions = payload.get("partitions")
    if not isinstance(partitions, list):
        raise ValueError("pinned DatasetVersion manifest partitions must be a list")
    calendar_partition = next(
        (
            item
            for item in partitions
            if isinstance(item, Mapping) and str(item.get("path") or "") == "calendars/day.txt"
        ),
        None,
    )
    if calendar_partition is None:
        raise ValueError("pinned DatasetVersion does not govern calendars/day.txt")
    expected = str(calendar_partition.get("sha256") or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise ValueError("pinned DatasetVersion calendar checksum is invalid")
    if sha256_file(calendar_path) != expected:
        raise ValueError("pinned DatasetVersion calendar checksum mismatch")
    return qlib_dates


def shared_research_calendar(settings: Settings) -> pd.DatetimeIndex:
    """Return governed open dates present in the pinned Qlib DatasetVersion."""

    from qlib_platform.datasets.dataset_resolver import pin_dataset

    settings, resolved = pin_dataset(settings)
    calendar_path = settings.qlib_data_uri / "calendars" / "day.txt"
    qlib_dates = _read_dates(calendar_path)
    if qlib_dates.empty:
        raise ValueError("Qlib calendar contains no trading dates")

    if settings.uses_platform_release():
        # Production research is owned by platform's immutable DataRelease. The
        # repository raw store must not be consulted as an undeclared second source.
        from qlib_platform.ops.platform_release import load_platform_release

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

    versioned_dates = _versioned_dataset_calendar(resolved, calendar_path, qlib_dates)
    if versioned_dates is not None:
        return versioned_dates

    # Legacy/unversioned development datasets predate the immutable DatasetVersion
    # contract. Keep the historical three-way guard for those inputs only.
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
