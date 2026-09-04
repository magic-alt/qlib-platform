from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

from .ingestion import Extractor
from ..settings import Settings
from ..symbols import ts_to_qlib


def build_sw2021_industry_intervals(members: pd.DataFrame, *, coverage_end: str) -> pd.DataFrame:
    required = {"l1_code", "l1_name", "ts_code", "in_date", "out_date"}
    missing = required - set(members.columns)
    if missing:
        raise ValueError(f"SW2021 member data is missing fields: {sorted(missing)}")
    frame = members.copy()
    frame["effective_from"] = pd.to_datetime(frame["in_date"], errors="raise").dt.normalize()
    terminal = pd.Timestamp(coverage_end).normalize()
    frame["effective_to"] = pd.to_datetime(frame["out_date"], errors="coerce").dt.normalize()
    frame["effective_to"] = frame["effective_to"].fillna(terminal).clip(upper=terminal)
    frame["instrument"] = frame["ts_code"].astype(str).map(ts_to_qlib)
    frame["industry_code"] = frame["l1_code"].astype(str).str.split(".").str[0]
    frame["industry_name"] = frame["l1_name"].astype(str)
    frame["taxonomy"] = "SW2021"
    frame["level_no"] = 1
    frame = frame.drop_duplicates(["instrument", "industry_code", "effective_from"], keep="last")
    frame = frame.sort_values(["instrument", "effective_from", "industry_code"]).reset_index(drop=True)
    adjusted: list[pd.DataFrame] = []
    for _, group in frame.groupby("instrument", sort=False):
        group = group.copy()
        starts = group["effective_from"].tolist()
        for index in range(len(group) - 1):
            next_start = starts[index + 1] - pd.Timedelta(days=1)
            row_index = group.index[index]
            group.loc[row_index, "effective_to"] = min(group.loc[row_index, "effective_to"], next_start)
        adjusted.append(group)
    result = pd.concat(adjusted, ignore_index=True) if adjusted else frame
    if result.empty:
        raise ValueError("SW2021 member data produced no PIT intervals")
    if (result["effective_to"] < result["effective_from"]).any():
        raise ValueError("SW2021 member data contains invalid or overlapping intervals")
    return result[
        [
            "instrument",
            "effective_from",
            "effective_to",
            "industry_code",
            "industry_name",
            "taxonomy",
            "level_no",
        ]
    ].sort_values(["instrument", "effective_from"])


def sync_sw2021_industry(
    settings: Settings,
    *,
    coverage_end: str,
    client: Any | None = None,
) -> Path:
    if client is None:
        client = Extractor(settings).client
    classification = client.call(
        "index_classify",
        required=True,
        level="L1",
        src="SW2021",
        fields="index_code,industry_name,parent_code,level,industry_code,is_pub,src",
    )
    required = {"index_code", "industry_name"}
    if classification.empty or not required.issubset(classification.columns):
        raise ValueError("index_classify returned no valid SW2021 L1 industries")
    frames: list[pd.DataFrame] = []
    fields = "l1_code,l1_name,l2_code,l2_name,l3_code,l3_name,ts_code,name,in_date,out_date,is_new"
    for code in sorted(classification["index_code"].dropna().astype(str).unique()):
        for is_new in ("Y", "N"):
            value = client.call(
                "index_member_all",
                required=True,
                l1_code=code,
                is_new=is_new,
                fields=fields,
            )
            if not value.empty:
                frames.append(value)
    if not frames:
        raise ValueError("index_member_all returned no SW2021 membership history")
    result = build_sw2021_industry_intervals(pd.concat(frames, ignore_index=True), coverage_end=coverage_end)
    target = settings.paths.metadata / "industry_classification_pit.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        result.to_parquet(temporary, index=False)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
