from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from qlib_platform.settings import Settings


PIT_FIELDS = (
    "roe_waa_pit",
    "roa_pit",
    "netprofit_margin_pit",
    "netprofit_yoy_pit",
    "or_yoy_pit",
    "debt_to_assets_pit",
    "ocf_to_or_pit",
)

# DataRelease v2 adds standardized point-in-time accounting primitives.  These
# are data facts (including TTM and prior-year comparable values), not Alpha
# scores; factor definitions remain owned by the Qlib research layer.
PIT_FIELDS_V2 = (
    *PIT_FIELDS,
    "total_assets_pit",
    "prior_year_total_assets_pit",
    "total_equity_pit",
    "prior_year_total_equity_pit",
    "gross_profit_ttm_pit",
    "prior_year_gross_profit_ttm_pit",
    "operating_profit_ttm_pit",
    "prior_year_operating_profit_ttm_pit",
    "operating_cash_flow_ttm_pit",
    "prior_year_operating_cash_flow_ttm_pit",
    "revenue_ttm_pit",
    "prior_year_revenue_ttm_pit",
    "parent_net_income_ttm_pit",
    "prior_year_parent_net_income_ttm_pit",
    "capex_ttm_pit",
    "fixed_assets_pit",
    "total_shares_pit",
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
    dates = pd.to_datetime(
        calendar.loc[pd.to_numeric(calendar["is_open"], errors="coerce") == 1, "cal_date"], errors="coerce"
    )
    dates = pd.DatetimeIndex(dates.dropna().sort_values().unique())
    records = reports.copy()
    records["ann_date"] = pd.to_datetime(records["ann_date"], errors="coerce").dt.normalize()
    records["end_date"] = pd.to_datetime(records["end_date"], errors="coerce").dt.normalize()
    if records[["ann_date", "end_date"]].isna().any().any():
        raise ValueError("fundamental reports contain invalid dates")
    if "f_ann_date" in records:
        final_announcement = pd.to_datetime(records["f_ann_date"], errors="coerce").dt.normalize()
        records["source_ann_date"] = pd.concat([records["ann_date"], final_announcement], axis=1).max(axis=1)
    else:
        records["source_ann_date"] = records["ann_date"]
    records["ingest_time"] = pd.to_datetime(
        records.get("ingest_time", pd.Series(pd.NaT, index=records.index)), errors="coerce", utc=True
    )
    update_flag = records.get("update_flag", pd.Series("0", index=records.index))
    records["update_flag"] = update_flag.astype(str)
    effective_positions = dates.searchsorted(records["source_ann_date"], side="right")
    records["effective_time"] = [
        dates[position] if position < len(dates) else pd.NaT for position in effective_positions
    ]
    records = records.dropna(subset=["effective_time"])
    rows: list[pd.DataFrame] = []
    open_days = pd.DataFrame({"trade_date": dates})
    for code, group in records.groupby("ts_code", sort=True):
        group = group.sort_values(
            ["effective_time", "end_date", "update_flag", "ingest_time"], na_position="first"
        ).drop_duplicates(["end_date", "effective_time"], keep="last")
        known_periods: dict[pd.Timestamp, pd.Series] = {}
        event_rows: list[dict[str, Any]] = []
        for effective_time, effective_group in group.groupby("effective_time", sort=True):
            for row in effective_group.itertuples(index=False):
                values = pd.Series(row._asdict())
                known_periods[pd.Timestamp(values["end_date"])] = values
            latest_period = max(known_periods)
            latest = known_periods[latest_period]
            event_rows.append(
                {
                    "effective_time": pd.Timestamp(effective_time),
                    "source_period": latest_period,
                    "source_ann_date": latest["source_ann_date"],
                    "ingest_time": latest["ingest_time"],
                    **{field: latest[field] for field in PIT_FIELDS},
                }
            )
        events = pd.DataFrame(event_rows)
        events[list(PIT_FIELDS)] = events[list(PIT_FIELDS)].apply(pd.to_numeric, errors="coerce")
        expanded = pd.merge_asof(
            open_days,
            events.sort_values("effective_time"),
            left_on="trade_date",
            right_on="effective_time",
            direction="backward",
            allow_exact_matches=True,
        )
        expanded = expanded.loc[expanded[list(PIT_FIELDS)].notna().any(axis=1)]
        expanded.insert(0, "ts_code", str(code))
        rows.append(expanded)
    return (
        pd.concat(rows, ignore_index=True)
        if rows
        else pd.DataFrame(
            columns=[
                "ts_code",
                "trade_date",
                "effective_time",
                "source_period",
                "source_ann_date",
                "ingest_time",
                *PIT_FIELDS,
            ]
        )
    )


def pit_fundamentals_path(settings: Settings) -> Path:
    paths = settings.paths
    gold = paths.gold / "pit" / "current" / "fundamentals_daily.parquet"
    legacy = paths.curated / "fundamentals_pit.parquet"
    return gold if gold.is_file() or not legacy.is_file() else legacy


def build_pit_from_extended(settings: Settings) -> Path:
    paths = settings.paths
    source_root = paths.raw / "extended" / "fina_indicator_vip"
    files = sorted(source_root.glob("trade_date=*/data.parquet"))
    if not files:
        raise FileNotFoundError(f"fina_indicator_vip Bronze partitions are missing: {source_root}")
    reports = pd.concat([pd.read_parquet(path) for path in files], ignore_index=True)
    valid_report = (
        reports["ts_code"].notna()
        & pd.to_datetime(reports["ann_date"], errors="coerce").notna()
        & pd.to_datetime(reports["end_date"], errors="coerce").notna()
    )
    reports = reports.loc[valid_report].copy()
    if reports.empty:
        raise ValueError("fina_indicator_vip contains no reports with valid identifiers and dates")
    mapping = {
        "roe_waa": "roe_waa_pit",
        "roa": "roa_pit",
        "netprofit_margin": "netprofit_margin_pit",
        "netprofit_yoy": "netprofit_yoy_pit",
        "or_yoy": "or_yoy_pit",
        "debt_to_assets": "debt_to_assets_pit",
    }
    if "ocf_to_or" not in reports.columns and "q_ocf_to_sales" not in reports.columns:
        raise ValueError("fina_indicator_vip is missing ocf_to_or and q_ocf_to_sales")
    reports = reports.rename(columns=mapping)
    cashflow_to_sales = reports.get("ocf_to_or")
    if cashflow_to_sales is None:
        cashflow_to_sales = reports["q_ocf_to_sales"]
    elif "q_ocf_to_sales" in reports.columns:
        cashflow_to_sales = cashflow_to_sales.combine_first(reports["q_ocf_to_sales"])
    reports["ocf_to_or_pit"] = cashflow_to_sales
    calendar = pd.read_parquet(paths.metadata / "trade_calendar.parquet")
    result = build_pit_fundamentals(reports, calendar)
    target = paths.gold / "pit" / "current" / "fundamentals_daily.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".parquet.tmp")
    result.to_parquet(temporary, index=False)
    temporary.replace(target)
    return target


def ingest_pit_fundamentals(
    reports_path: str | Path, calendar_path: str | Path, output_path: str | Path
) -> Path:
    result = build_pit_fundamentals(pd.read_parquet(reports_path), pd.read_parquet(calendar_path))
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(target, index=False)
    return target
