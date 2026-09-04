from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

import pandas as pd

from qlib_platform.settings import Settings
from qlib_platform.data.store import sha256_file
from qlib_platform.data.symbols import ts_to_qlib

MEMBERSHIP_COLUMNS = [
    "universe_code",
    "instrument",
    "snapshot_date",
    "effective_from",
    "effective_to",
    "weight",
]


def configured_universe(settings: Settings) -> tuple[str, str, Path] | None:
    config = settings.data.get("universe", {})
    if not isinstance(config, Mapping):
        return None
    name = str(config.get("instruments", "all")).strip().lower()
    if name == "all":
        return None
    index_code = str(config.get("index_code", "")).strip().upper()
    if not index_code:
        raise ValueError("universe.index_code is required for a point-in-time universe")
    configured_path = config.get("membership_file")
    path = (
        Path(str(configured_path)).expanduser()
        if configured_path
        else settings.paths.metadata / "universe_membership" / f"{name}.parquet"
    )
    if not path.is_absolute():
        path = (settings.config_path.parent / path).resolve()
    return name, index_code, path


def build_membership_intervals(
    snapshots: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    *,
    universe_code: str,
    effective_lag_days: int = 1,
) -> pd.DataFrame:
    """Convert index snapshots into conservative point-in-time membership intervals.

    A snapshot becomes usable only after ``effective_lag_days`` open sessions.  This
    avoids letting a same-day close snapshot affect a signal formed on that close.
    Missing historical snapshots are never filled from a later snapshot.
    """

    required = {"con_code", "trade_date"}
    missing = required - set(snapshots.columns)
    if missing:
        raise ValueError(f"universe snapshots missing columns: {sorted(missing)}")
    if effective_lag_days < 1:
        raise ValueError("universe membership effective_lag_days must be at least 1")
    dates = (
        pd.DatetimeIndex(pd.to_datetime(calendar, errors="coerce"))
        .dropna()
        .normalize()
        .unique()
        .sort_values()
    )
    if dates.empty:
        raise ValueError("cannot build universe membership without an open trading calendar")

    frame = snapshots.copy()
    frame["snapshot_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["snapshot_date", "con_code"])
    frame["instrument"] = frame["con_code"].astype(str).map(ts_to_qlib)
    frame["weight"] = pd.to_numeric(frame.get("weight"), errors="coerce")
    frame = frame.sort_values(["snapshot_date", "instrument"]).drop_duplicates(
        ["snapshot_date", "instrument"], keep="last"
    )
    if frame.empty:
        raise ValueError("universe snapshots contain no valid members")

    snapshot_dates = pd.DatetimeIndex(frame["snapshot_date"].unique()).sort_values()
    effective: dict[pd.Timestamp, pd.Timestamp] = {}
    for snapshot_date in snapshot_dates:
        insertion = int(dates.searchsorted(snapshot_date, side="right"))
        target = insertion + effective_lag_days - 1
        if target < len(dates):
            effective[pd.Timestamp(snapshot_date)] = pd.Timestamp(dates[target])
    if not effective:
        raise ValueError("all universe snapshots occur after the available trading calendar")

    ordered = sorted(effective)
    records: list[dict[str, object]] = []
    for position, snapshot_date in enumerate(ordered):
        start = effective[snapshot_date]
        if position + 1 < len(ordered):
            next_start = effective[ordered[position + 1]]
            next_index = int(dates.get_indexer([next_start])[0])
            end = pd.Timestamp(dates[next_index - 1])
        else:
            end = pd.Timestamp(dates[-1])
        if end < start:
            continue
        members = frame.loc[frame["snapshot_date"] == snapshot_date]
        for row in members.itertuples(index=False):
            records.append(
                {
                    "universe_code": universe_code,
                    "instrument": row.instrument,
                    "snapshot_date": snapshot_date,
                    "effective_from": start,
                    "effective_to": end,
                    "weight": row.weight,
                }
            )
    result = pd.DataFrame.from_records(records, columns=MEMBERSHIP_COLUMNS)
    if result.empty:
        raise ValueError("universe membership has no effective intervals")
    return result.sort_values(["instrument", "effective_from"]).reset_index(drop=True)


def build_membership_from_source_intervals(
    source_intervals: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    *,
    universe_code: str,
    effective_lag_days: int = 1,
) -> pd.DataFrame:
    """Normalize Lean's governed PIT intervals without reinterpreting them as snapshots."""

    required = {"symbol", "start_date", "end_date", "announce_date", "effective_date"}
    missing = required - set(source_intervals.columns)
    if missing:
        raise ValueError(f"source universe intervals missing columns: {sorted(missing)}")
    if effective_lag_days < 1:
        raise ValueError("universe membership effective_lag_days must be at least 1")
    dates = (
        pd.DatetimeIndex(pd.to_datetime(calendar, errors="coerce"))
        .dropna()
        .normalize()
        .unique()
        .sort_values()
    )
    if dates.empty:
        raise ValueError("cannot build universe membership without an open trading calendar")

    records: list[dict[str, object]] = []
    for row in source_intervals.itertuples(index=False):
        disclosed = [
            pd.Timestamp(value).normalize()
            for value in (row.start_date, row.announce_date, row.effective_date)
            if pd.notna(value) and str(value).strip()
        ]
        if not disclosed:
            continue
        snapshot_date = max(disclosed)
        insertion = int(dates.searchsorted(snapshot_date, side="right"))
        start_index = insertion + effective_lag_days - 1
        if start_index >= len(dates):
            continue
        effective_from = pd.Timestamp(dates[start_index])
        raw_end = pd.Timestamp(row.end_date).normalize() if pd.notna(row.end_date) else dates[-1]
        end_index = int(dates.searchsorted(raw_end, side="right")) - 1
        if end_index < 0:
            continue
        effective_to = pd.Timestamp(dates[min(end_index, len(dates) - 1)])
        if effective_to < effective_from:
            continue
        symbol = str(row.symbol).strip().upper()
        suffix = "SH" if symbol[:1] in {"5", "6", "9"} else "BJ" if symbol[:1] in {"4", "8"} else "SZ"
        records.append(
            {
                "universe_code": universe_code,
                "instrument": ts_to_qlib(f"{symbol}.{suffix}"),
                "snapshot_date": snapshot_date,
                "effective_from": effective_from,
                "effective_to": effective_to,
                "weight": pd.to_numeric(getattr(row, "weight", None), errors="coerce"),
            }
        )
    result = pd.DataFrame.from_records(records, columns=MEMBERSHIP_COLUMNS)
    if result.empty:
        raise ValueError("source universe membership has no effective intervals")
    return (
        result.sort_values(["instrument", "effective_from"])
        .drop_duplicates(["instrument", "effective_from"], keep="last")
        .reset_index(drop=True)
    )


def write_membership(settings: Settings, intervals: pd.DataFrame) -> Path:
    configured = configured_universe(settings)
    if configured is None:
        raise ValueError("cannot write point-in-time membership for universe.instruments=all")
    _, _, path = configured
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    intervals.to_parquet(tmp, index=False)
    os.replace(tmp, path)
    return path


def membership_fingerprint(settings: Settings) -> str | None:
    configured = configured_universe(settings)
    if configured is None:
        return None
    path = configured[2]
    if not path.is_file():
        raise FileNotFoundError(f"point-in-time universe membership is missing: {path}")
    return sha256_file(path)


def install_qlib_universe(settings: Settings, dataset_dir: Path) -> Path | None:
    configured = configured_universe(settings)
    if configured is None:
        return None
    name, _, source = configured
    if not source.is_file():
        raise FileNotFoundError(f"point-in-time universe membership is missing: {source}")
    intervals = pd.read_parquet(source)
    required = {"instrument", "effective_from", "effective_to"}
    missing = required - set(intervals.columns)
    if missing:
        raise ValueError(f"universe membership missing columns: {sorted(missing)}")
    intervals_dir = dataset_dir / "instruments"
    intervals_dir.mkdir(parents=True, exist_ok=True)
    target = intervals_dir / f"{name}.txt"
    tmp = target.with_suffix(".txt.tmp")
    lines = [
        f"{row.instrument}\t{pd.Timestamp(row.effective_from):%Y-%m-%d}\t{pd.Timestamp(row.effective_to):%Y-%m-%d}"
        for row in intervals.sort_values(["instrument", "effective_from"]).itertuples(index=False)
    ]
    if not lines:
        raise ValueError("point-in-time universe membership is empty")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target
