from __future__ import annotations

from pathlib import Path

import pandas as pd


PIT_FIELDS = ("roe_waa_pit", "roa_pit", "netprofit_margin_pit", "netprofit_yoy_pit", "or_yoy_pit", "debt_to_assets_pit", "ocf_to_or_pit")


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
    for code, group in records.sort_values(["ann_date", "end_date"]).groupby("ts_code", sort=True):
        indexed = group.set_index("ann_date")[list(PIT_FIELDS)].apply(pd.to_numeric, errors="coerce")
        # Multiple report periods announced on a day are resolved deterministically
        # by the latest period, then carried forward only to future open dates.
        indexed = indexed[~indexed.index.duplicated(keep="last")]
        expanded = indexed.reindex(dates).ffill()
        expanded = expanded.loc[expanded.notna().any(axis=1)].reset_index(names="trade_date")
        expanded.insert(0, "ts_code", code)
        rows.append(expanded)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["ts_code", "trade_date", *PIT_FIELDS])


def ingest_pit_fundamentals(reports_path: str | Path, calendar_path: str | Path, output_path: str | Path) -> Path:
    result = build_pit_fundamentals(pd.read_parquet(reports_path), pd.read_parquet(calendar_path))
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(target, index=False)
    return target
