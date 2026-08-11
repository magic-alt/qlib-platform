from __future__ import annotations

from pathlib import Path

import pandas as pd


PIT_FIELDS = (
    "roe_waa_pit",
    "roa_pit",
    "netprofit_margin_pit",
    "netprofit_yoy_pit",
    "or_yoy_pit",
    "debt_to_assets_pit",
    "ocf_to_or_pit",
)


def build_pit_fundamentals(reports: pd.DataFrame, calendar: pd.DataFrame) -> pd.DataFrame:
    """Expand announced financial reports only from their public announce date.

    Input is an ingestion contract, not a model convenience table: each row
    must identify the security, report period, and announcement date.  Later
    restatements supersede earlier values only from their own announcement date.
    """
    required = {"ts_code", "end_date", "ann_date", *PIT_FIELDS}
    missing = required - set(reports.columns)
    if missing:
        raise ValueError(f"fundamental reports missing columns: {sorted(missing)}")
    if "cal_date" not in calendar or "is_open" not in calendar:
        raise ValueError("calendar must contain cal_date and is_open")
    dates = pd.to_datetime(calendar.loc[pd.to_numeric(calendar["is_open"], errors="coerce") == 1, "cal_date"], errors="coerce")
    dates = pd.DatetimeIndex(dates.dropna().sort_values().unique())
    records = reports.copy()
    records["ann_date"] = pd.to_datetime(records["ann_date"], errors="coerce").dt.normalize()
    records["end_date"] = pd.to_datetime(records["end_date"], errors="coerce").dt.normalize()
    if records[["ann_date", "end_date"]].isna().any().any():
        raise ValueError("fundamental reports contain invalid dates")
    rows: list[pd.DataFrame] = []
    open_days = pd.DataFrame({"trade_date": dates})
    for code, group in records.sort_values(["ann_date", "end_date"]).groupby("ts_code", sort=True):
        events = group.drop_duplicates("ann_date", keep="last")[["ann_date", *PIT_FIELDS]].copy()
        events[list(PIT_FIELDS)] = events[list(PIT_FIELDS)].apply(pd.to_numeric, errors="coerce")
        # A weekend/holiday announcement becomes usable on the first following
        # open day.  merge_asof also ensures a restatement only supersedes the
        # previously known values after its own announcement timestamp.
        expanded = pd.merge_asof(
            open_days,
            events.sort_values("ann_date"),
            left_on="trade_date",
            right_on="ann_date",
            direction="backward",
            allow_exact_matches=True,
        ).drop(columns="ann_date")
        expanded = expanded.loc[expanded[list(PIT_FIELDS)].notna().any(axis=1)]
        expanded.insert(0, "ts_code", str(code))
        rows.append(expanded)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["ts_code", "trade_date", *PIT_FIELDS])


def ingest_pit_fundamentals(reports_path: str | Path, calendar_path: str | Path, output_path: str | Path) -> Path:
    result = build_pit_fundamentals(pd.read_parquet(reports_path), pd.read_parquet(calendar_path))
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(target, index=False)
    return target
