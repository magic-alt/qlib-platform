from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from loguru import logger

from .client import RetryPolicy, TushareClient
from .settings import Settings
from .store import PartitionStore


DAILY_FIELDS = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
ADJ_FIELDS = "ts_code,trade_date,adj_factor"
BASIC_FIELDS = (
    "ts_code,trade_date,close,turnover_rate,turnover_rate_f,volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,"
    "dv_ratio,dv_ttm,total_share,float_share,free_share,total_mv,circ_mv,limit_status"
)
MONEYFLOW_FIELDS = (
    "ts_code,trade_date,buy_lg_vol,buy_lg_amount,sell_lg_vol,sell_lg_amount,"
    "buy_elg_vol,buy_elg_amount,sell_elg_vol,sell_elg_amount,net_mf_vol,net_mf_amount"
)
LIMIT_FIELDS = "ts_code,trade_date,pre_close,up_limit,down_limit"
SUSPEND_FIELDS = "ts_code,trade_date,suspend_timing,suspend_type"
ST_FIELDS = "ts_code,name,trade_date,type,type_name"


@dataclass(frozen=True)
class Endpoint:
    name: str
    fields: str
    required: bool


class Extractor:
    def __init__(self, settings: Settings):
        cfg = settings.data["tushare"]
        calls = int(__import__("os").getenv("TUSHARE_CALLS_PER_MINUTE", cfg["calls_per_minute"]))
        self.settings = settings
        self.client = TushareClient(
            settings.tushare_token,
            calls_per_minute=calls,
            retry_policy=RetryPolicy(int(cfg["max_attempts"]), float(cfg["base_sleep_seconds"])),
        )
        self.store = PartitionStore(settings.paths.raw)
        optional = cfg.get("optional_endpoints", {})
        self.endpoints = [
            Endpoint("daily", DAILY_FIELDS, True),
            Endpoint("adj_factor", ADJ_FIELDS, True),
            Endpoint("daily_basic", BASIC_FIELDS, True),
            Endpoint("moneyflow", MONEYFLOW_FIELDS, bool(optional.get("moneyflow", True))),
            Endpoint("stk_limit", LIMIT_FIELDS, bool(optional.get("stk_limit", True))),
            Endpoint("suspend_d", SUSPEND_FIELDS, bool(optional.get("suspend_d", True))),
            Endpoint("stock_st", ST_FIELDS, bool(optional.get("stock_st", True))),
        ]

    def fetch_stock_master(self) -> pd.DataFrame:
        fields = (
            "ts_code,symbol,name,area,industry,market,exchange,list_status,list_date,delist_date,is_hs,"
            "act_name,act_ent_type"
        )
        frames = []
        for status in ("L", "D", "P", "G"):
            df = self.client.call("stock_basic", fields=fields, required=True, exchange="", list_status=status)
            if not df.empty:
                frames.append(df)
        master = pd.concat(frames, ignore_index=True).drop_duplicates("ts_code", keep="last")
        master["list_date"] = pd.to_datetime(master["list_date"], errors="coerce")
        master["delist_date"] = pd.to_datetime(master["delist_date"], errors="coerce")
        path = self.settings.paths.metadata / "stock_master.parquet"
        master.to_parquet(path, index=False)
        logger.info("Saved stock master: {} rows -> {}", len(master), path)
        return master

    def fetch_calendar(self, start_date: str, end_date: str) -> pd.DataFrame:
        cal = self.client.call(
            "trade_cal", required=True, exchange="SSE", start_date=start_date, end_date=end_date,
            fields="exchange,cal_date,is_open,pretrade_date",
        )
        cal["cal_date"] = pd.to_datetime(cal["cal_date"], errors="raise")
        cal["is_open"] = cal["is_open"].astype(int)
        path = self.settings.paths.metadata / "trade_calendar.parquet"
        cal.to_parquet(path, index=False)
        logger.info("Saved trade calendar: {} rows -> {}", len(cal), path)
        return cal

    def open_dates(self, start_date: str, end_date: str) -> list[str]:
        path = self.settings.paths.metadata / "trade_calendar.parquet"
        cal = pd.read_parquet(path) if path.exists() else self.fetch_calendar(start_date, end_date)
        mask = (cal["is_open"] == 1) & (cal["cal_date"] >= pd.Timestamp(start_date)) & (
            cal["cal_date"] <= pd.Timestamp(end_date)
        )
        return cal.loc[mask, "cal_date"].dt.strftime("%Y%m%d").tolist()

    def fetch_day(self, trade_date: str, force: bool = False) -> None:
        for ep in self.endpoints:
            if not force and self.store.exists(ep.name, trade_date):
                continue
            required = ep.required if ep.name in {"daily", "adj_factor", "daily_basic"} else False
            params = {"trade_date": trade_date}
            if ep.name == "suspend_d":
                params["suspend_type"] = "S"
            df = self.client.call(ep.name, fields=ep.fields, required=required, **params)
            self.store.write(ep.name, trade_date, df, {"api": ep.name, "trade_date": trade_date})
            logger.info("{} {}: {} rows", trade_date, ep.name, len(df))

    def backfill(self, start_date: str, end_date: str, force: bool = False) -> None:
        if not (self.settings.paths.metadata / "stock_master.parquet").exists():
            self.fetch_stock_master()
        dates = self.open_dates(start_date, end_date)
        for i, trade_date in enumerate(dates, 1):
            logger.info("Backfill {}/{}: {}", i, len(dates), trade_date)
            self.fetch_day(trade_date, force=force)
