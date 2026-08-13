"""Resumable ingestion for non-intraday A-share Tushare Pro domains."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Literal

import pandas as pd
from loguru import logger

from .client import FetchResult
from .extract import Extractor
from .settings import Settings
from .store import PartitionStore

Plan = Literal["trade_date", "report_period", "date_range", "symbol", "exchange", "global"]


@dataclass(frozen=True)
class ExtendedEndpoint:
    name: str
    group: str
    plan: Plan
    min_date: str = "19900101"
    exchanges: tuple[str, ...] = ()
    fixed_params: tuple[tuple[str, str], ...] = ()

    period_parameter: str = "period"

EXTENDED_ENDPOINTS: tuple[ExtendedEndpoint, ...] = (
    ExtendedEndpoint("stock_company", "basic", "exchange", exchanges=("SSE", "SZSE", "BSE")),
    ExtendedEndpoint("namechange", "basic", "symbol"),
    ExtendedEndpoint("new_share", "basic", "global"),
    ExtendedEndpoint("income_vip", "financial", "report_period"),
    ExtendedEndpoint("balancesheet_vip", "financial", "report_period"),
    ExtendedEndpoint("cashflow_vip", "financial", "report_period"),
    ExtendedEndpoint("fina_indicator_vip", "financial", "report_period"),
    ExtendedEndpoint("forecast_vip", "financial", "report_period"),
    ExtendedEndpoint("express_vip", "financial", "report_period"),
    ExtendedEndpoint("fina_mainbz_vip", "financial", "report_period"),
    ExtendedEndpoint("disclosure_date", "financial", "report_period", period_parameter="end_date"),
    ExtendedEndpoint("share_float", "corporate_action", "date_range"),
    ExtendedEndpoint("dividend", "corporate_action", "symbol"),
    ExtendedEndpoint("repurchase", "corporate_action", "date_range"),
    ExtendedEndpoint("pledge_stat", "corporate_action", "symbol"),
    ExtendedEndpoint("pledge_detail", "corporate_action", "symbol"),
    ExtendedEndpoint("stk_holdernumber", "holder", "date_range"),
    ExtendedEndpoint("top10_holders", "holder", "date_range"),
    ExtendedEndpoint("top10_floatholders", "holder", "date_range"),
    ExtendedEndpoint("stk_managers", "holder", "symbol"),
    ExtendedEndpoint("stk_rewards", "holder", "symbol"),
    ExtendedEndpoint("limit_list_d", "market_reference", "trade_date", min_date="20160101"),
    ExtendedEndpoint("block_trade", "market_reference", "trade_date", min_date="20100101"),
    ExtendedEndpoint("top_list", "market_reference", "trade_date", min_date="20000101"),
    ExtendedEndpoint("margin", "market_reference", "trade_date", min_date="20100101"),
    ExtendedEndpoint("margin_detail", "market_reference", "trade_date", min_date="20100101"),
    ExtendedEndpoint("hsgt_moneyflow", "market_reference", "trade_date", min_date="20141117"),
    ExtendedEndpoint("hsgt_top10", "market_reference", "trade_date", min_date="20141117"),
)
EXTENDED_GROUPS = tuple(sorted({endpoint.group for endpoint in EXTENDED_ENDPOINTS}))


def _safe_partition(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _report_periods(start_date: str, end_date: str) -> list[str]:
    periods = pd.date_range(start=pd.Timestamp(start_date), end=pd.Timestamp(end_date), freq="QE-DEC")
    return [value.strftime("%Y%m%d") for value in periods]

def _month_windows(start_date: str, end_date: str) -> list[tuple[str, str]]:
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    cursor = start.replace(day=1)
    windows: list[tuple[str, str]] = []
    while cursor <= end:
        window_end = min(cursor + pd.offsets.MonthEnd(0), end)
        windows.append((max(cursor, start).strftime("%Y%m%d"), window_end.strftime("%Y%m%d")))
        cursor += pd.offsets.MonthBegin(1)
    return windows

class ExtendedDataBackfill:
    """Download wider A-share data domains with partition-level resumption."""

    def __init__(self, settings: Settings, *, client: Any | None = None, stock_master: pd.DataFrame | None = None, open_dates: Callable[[str, str], list[str]] | None = None) -> None:
        self.settings = settings
        self.store = PartitionStore(settings.paths.raw / "extended")
        self._extractor: Extractor | None = None
        if client is None or open_dates is None:
            self._extractor = Extractor(settings)
        self.client = client if client is not None else self._extractor.client
        self._stock_master = stock_master
        self._open_dates = open_dates if open_dates is not None else self._extractor.open_dates

    def _master(self) -> pd.DataFrame:
        if self._stock_master is not None:
            return self._stock_master
        path = self.settings.paths.metadata / "stock_master.parquet"
        if path.is_file():
            self._stock_master = pd.read_parquet(path)
        else:
            assert self._extractor is not None
            self._stock_master = self._extractor.fetch_stock_master()
        return self._stock_master

    def _tasks(self, endpoint: ExtendedEndpoint, start_date: str, end_date: str):
        start = max(start_date, endpoint.min_date)
        if start > end_date:
            return
        fixed = dict(endpoint.fixed_params)
        if endpoint.plan == "trade_date":
            for trade_date in self._open_dates(start, end_date):
                yield trade_date, {**fixed, "trade_date": trade_date}
        elif endpoint.plan == "report_period":
            for period in _report_periods(start, end_date):
                yield period, {**fixed, endpoint.period_parameter: period}
        elif endpoint.plan == "date_range":
            for window_start, window_end in _month_windows(start, end_date):
                yield f"{window_start}_{window_end}", {**fixed, "start_date": window_start, "end_date": window_end}
        elif endpoint.plan == "symbol":
            master = self._master()
            if "ts_code" not in master:
                raise ValueError("stock master must include ts_code")
            for symbol in sorted(set(master["ts_code"].dropna().astype(str).str.upper())):
                yield _safe_partition(symbol), {**fixed, "ts_code": symbol}
        elif endpoint.plan == "exchange":
            for exchange in endpoint.exchanges:
                yield exchange, {**fixed, "exchange": exchange}
        else:
            yield "all", fixed

    def _write_run_state(self, payload: dict[str, Any]) -> None:
        path = self.settings.paths.state / "extended_backfill" / "last_run.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def backfill(self, start_date: str, end_date: str, *, groups: Iterable[str] | None = None, force: bool = False) -> dict[str, Any]:
        requested_groups = set(groups or EXTENDED_GROUPS)
        unknown_groups = requested_groups - set(EXTENDED_GROUPS)
        if unknown_groups:
            raise ValueError(f"unknown extended data groups: {sorted(unknown_groups)}")
        counters = {"success": 0, "empty": 0, "permission_denied": 0, "failed": 0, "skipped": 0}
        started = datetime.now(timezone.utc).isoformat()
        self._write_run_state({"status": "running", "started_at_utc": started, "groups": sorted(requested_groups)})
        try:
            for endpoint in (item for item in EXTENDED_ENDPOINTS if item.group in requested_groups):
                for partition, params in self._tasks(endpoint, start_date, end_date):
                    if not force and self.store.is_terminal(endpoint.name, partition):
                        counters["skipped"] += 1
                        continue
                    result: FetchResult = self.client.fetch(endpoint.name, required=False, **params)
                    metadata = {"api": endpoint.name, "group": endpoint.group, "partition": partition, "params": params, "attempts": result.attempts, "error": result.error, "requested_at_utc": datetime.now(timezone.utc).isoformat()}
                    if result.succeeded:
                        self.store.write(endpoint.name, partition, result.data, metadata, status=result.status)
                    else:
                        self.store.write_status(endpoint.name, partition, status=result.status, metadata=metadata)
                    counters[result.status] = counters.get(result.status, 0) + 1
                    logger.info("Extended {} {}: status={}, rows={}", endpoint.name, partition, result.status, len(result.data))
        except Exception as exc:
            payload = {"status": "failed", "started_at_utc": started, "finished_at_utc": datetime.now(timezone.utc).isoformat(), "groups": sorted(requested_groups), "counters": counters, "error": str(exc)}
            self._write_run_state(payload)
            raise
        payload = {"status": "complete", "started_at_utc": started, "finished_at_utc": datetime.now(timezone.utc).isoformat(), "groups": sorted(requested_groups), "counters": counters}
        self._write_run_state(payload)
        return payload
