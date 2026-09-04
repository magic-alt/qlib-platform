from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from qlib_platform.settings import Settings
from qlib_platform.data.store import frame_content_sha256, sha256_file

DIVIDEND_FIELDS = (
    "ts_code,end_date,ann_date,div_proc,stk_div,stk_bo_rate,stk_co_rate,"
    "cash_div,cash_div_tax,record_date,ex_date,pay_date,div_listdate,"
    "imp_ann_date,base_date,base_share"
)
DIVIDEND_KEY = ["ts_code", "end_date", "ann_date", "div_proc"]
DIVIDEND_DATE_COLUMNS = [
    "end_date",
    "ann_date",
    "record_date",
    "ex_date",
    "pay_date",
    "div_listdate",
    "imp_ann_date",
    "base_date",
]


def _atomic_json(payload: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return path


def _normalise_dividends(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in DIVIDEND_FIELDS.split(","):
        if column not in result:
            result[column] = pd.NA
    result = result[DIVIDEND_FIELDS.split(",")]
    result["ts_code"] = result["ts_code"].astype("string").str.upper().str.strip()
    result["div_proc"] = result["div_proc"].astype("string").fillna("").str.strip()
    for column in DIVIDEND_DATE_COLUMNS:
        values = pd.to_datetime(result[column], errors="coerce")
        result[column] = values.dt.strftime("%Y%m%d").astype("string")
    for column in set(result.columns) - {"ts_code", "div_proc", *DIVIDEND_DATE_COLUMNS}:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return (
        result.dropna(subset=["ts_code"])
        .drop_duplicates(DIVIDEND_KEY, keep="last")
        .sort_values(["ts_code", "end_date", "ann_date", "div_proc"], kind="stable")
        .reset_index(drop=True)
    )


class CorporateActionStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.paths.raw / "dividend"

    def data_path(self, ts_code: str) -> Path:
        return self.root / f"ts_code={ts_code.upper()}" / "data.parquet"

    def manifest_path(self, ts_code: str) -> Path:
        return self.data_path(ts_code).with_name("manifest.json")

    def read(self, ts_code: str) -> pd.DataFrame:
        path = self.data_path(ts_code)
        return pd.read_parquet(path) if path.is_file() else pd.DataFrame(columns=DIVIDEND_FIELDS.split(","))

    def upsert(self, incoming: pd.DataFrame, *, check_only: bool = False) -> dict[str, Any]:
        incoming = _normalise_dividends(incoming)
        changed: list[str] = []
        rows = 0
        for ts_code, additions in incoming.groupby("ts_code", sort=True):
            code = str(ts_code)
            current = _normalise_dividends(self.read(code))
            merged = _normalise_dividends(pd.concat([current, additions], ignore_index=True))
            current_hash = frame_content_sha256(current, key_columns=DIVIDEND_KEY)
            merged_hash = frame_content_sha256(merged, key_columns=DIVIDEND_KEY)
            if current_hash == merged_hash:
                continue
            changed.append(code)
            rows += len(merged)
            if check_only:
                continue
            target = self.data_path(code)
            manifest = self.manifest_path(code)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".parquet.tmp")
            merged.to_parquet(temporary, index=False)
            os.replace(temporary, target)
            _atomic_json(
                {
                    "schema_version": "1.0",
                    "ts_code": code,
                    "rows": len(merged),
                    "content_sha256": merged_hash,
                    "file_sha256": sha256_file(target),
                    "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                },
                manifest,
            )
        return {"changed_symbols": changed, "changed_symbol_count": len(changed), "rows": rows}

    def sync_incremental(
        self,
        client: Any,
        *,
        as_of: str | pd.Timestamp,
        lookback_calendar_days: int = 5,
        full_symbols: Iterable[str] = (),
        check_only: bool = False,
    ) -> dict[str, Any]:
        if lookback_calendar_days < 1:
            raise ValueError("lookback_calendar_days must be positive")
        end = pd.Timestamp(as_of).normalize()
        dates = pd.date_range(end=end, periods=lookback_calendar_days, freq="D")
        frames: list[pd.DataFrame] = []
        calls = 0
        for date in dates:
            key = date.strftime("%Y%m%d")
            for parameter in ("ann_date", "imp_ann_date", "ex_date"):
                frame = client.call("dividend", fields=DIVIDEND_FIELDS, required=True, **{parameter: key})
                calls += 1
                if not frame.empty:
                    frames.append(frame)
        for symbol in sorted({str(value).upper() for value in full_symbols}):
            frame = client.call("dividend", fields=DIVIDEND_FIELDS, required=True, ts_code=symbol)
            calls += 1
            if not frame.empty:
                frames.append(frame)
        incoming = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        result = self.upsert(incoming, check_only=check_only)
        result.update({"api_calls": calls, "fetched_rows": len(incoming)})
        return result

    def bootstrap(self, client: Any, stock_master: pd.DataFrame, *, resume: bool = True) -> dict[str, Any]:
        if "ts_code" not in stock_master:
            raise ValueError("stock master must contain ts_code")
        checkpoint = self.settings.paths.state / "daily_sync" / "dividend_bootstrap.json"
        completed: set[str] = set()
        if resume and checkpoint.is_file():
            loaded = json.loads(checkpoint.read_text(encoding="utf-8"))
            completed = {str(value) for value in loaded.get("completed", [])}
        symbols = sorted(set(stock_master["ts_code"].dropna().astype(str).str.upper()) - completed)
        changed: set[str] = set()
        for position, symbol in enumerate(symbols, 1):
            frame = client.call("dividend", fields=DIVIDEND_FIELDS, required=True, ts_code=symbol)
            changed.update(self.upsert(frame)["changed_symbols"])
            completed.add(symbol)
            _atomic_json(
                {
                    "schema_version": "1.0",
                    "status": "running",
                    "completed": sorted(completed),
                    "remaining": len(symbols) - position,
                    "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                },
                checkpoint,
            )
        payload = {
            "schema_version": "1.0",
            "status": "complete",
            "completed": sorted(completed),
            "remaining": 0,
            "changed_symbol_count": len(changed),
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        _atomic_json(payload, checkpoint)
        return payload
