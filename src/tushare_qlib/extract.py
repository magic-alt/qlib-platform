from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd
from loguru import logger

from .client import RetryPolicy, TushareClient
from .mysql_source import (
    MysqlClient,
    build_connection_kwargs,
    build_lean_canonical_range_endpoints,
    build_mysql_endpoints,
    fetch_lean_benchmark,
    fetch_lean_universe_intervals,
    lean_mysql_preflight,
)
from .quality import assert_quality, validate_raw_day, write_report
from .settings import Settings
from .store import PartitionStore
from .universe import (
    build_membership_from_source_intervals,
    build_membership_intervals,
    configured_universe,
    write_membership,
)

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
INDEX_DAILY_FIELDS = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
INDEX_DAILY_FIELDS = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"


@dataclass(frozen=True)
class Endpoint:
    name: str
    fields: str
    required: bool
    enabled: bool = True


class Extractor:
    def _is_mysql_source(self, settings: Settings) -> bool:
        source = settings.data.get("data_source", {})
        if not isinstance(source, Mapping):
            return False
        kind = str(source.get("kind", "tushare")).strip().lower()
        if kind in {"mysql", "lean_mysql", "lean-platform", "lean_platform"}:
            return True
        if kind == "auto":
            return bool(source.get("mysql"))
        return False

    def __init__(self, settings: Settings):
        cfg = settings.data["tushare"]
        calls = int(os.getenv("TUSHARE_CALLS_PER_MINUTE", cfg.get("calls_per_minute", 180)))
        self.settings = settings
        self.store = PartitionStore(settings.paths.raw)
        self.source_is_mysql = self._is_mysql_source(settings)
        self.client: TushareClient | MysqlClient
        optional = cfg.get("optional_endpoints", {})
        mysql_endpoint_cfg: dict[str, dict[str, Any]] = {}

        policy = RetryPolicy(
            int(cfg.get("max_attempts", 6)),
            float(cfg.get("base_sleep_seconds", 2.0)),
            float(cfg.get("max_sleep_seconds", 60.0)),
            float(cfg.get("jitter_ratio", 0.15)),
        )
        if self.source_is_mysql:
            source_cfg = settings.data.get("data_source", {})
            mysql_cfg = source_cfg.get("mysql") if isinstance(source_cfg, Mapping) else None
            if not isinstance(mysql_cfg, Mapping):
                raise ValueError("data_source.kind=mysql requires data_source.mysql configuration")
            mysql_endpoint_cfg = build_mysql_endpoints(mysql_cfg, optional_endpoints=optional)
            self.client = MysqlClient(
                connection=build_connection_kwargs(mysql_cfg),
                endpoint_queries={name: value["query"] for name, value in mysql_endpoint_cfg.items()},
                default_params={
                    "source": str(mysql_cfg.get("source", "tushare")).strip(),
                    "universe": str(mysql_cfg.get("universe", "CSI300")).strip(),
                },
                retry_policy=policy,
            )
        else:
            self.client = TushareClient(
                settings.require_token(),
                calls_per_minute=calls,
                retry_policy=policy,
            )
        self.endpoints = [
            Endpoint(
                "daily",
                DAILY_FIELDS,
                True,
                enabled=(bool(optional.get("daily", True)) if isinstance(optional, Mapping) else True),
            ),
            Endpoint(
                "adj_factor",
                ADJ_FIELDS,
                True,
                enabled=(bool(optional.get("adj_factor", True)) if isinstance(optional, Mapping) else True),
            ),
            Endpoint(
                "daily_basic",
                BASIC_FIELDS,
                True,
                enabled=(bool(optional.get("daily_basic", True)) if isinstance(optional, Mapping) else True),
            ),
            Endpoint(
                "moneyflow",
                MONEYFLOW_FIELDS,
                False,
                enabled=(bool(optional.get("moneyflow", True)) if isinstance(optional, Mapping) else True),
            ),
            Endpoint(
                "stk_limit",
                LIMIT_FIELDS,
                False,
                enabled=(bool(optional.get("stk_limit", True)) if isinstance(optional, Mapping) else True),
            ),
            Endpoint(
                "suspend_d",
                SUSPEND_FIELDS,
                False,
                enabled=(bool(optional.get("suspend_d", True)) if isinstance(optional, Mapping) else True),
            ),
            Endpoint(
                "stock_st",
                ST_FIELDS,
                False,
                enabled=(bool(optional.get("stock_st", True)) if isinstance(optional, Mapping) else True),
            ),
        ]

        if self.source_is_mysql and mysql_endpoint_cfg:
            for idx, endpoint in enumerate(self.endpoints):
                configured = mysql_endpoint_cfg.get(endpoint.name)
                if configured:
                    self.endpoints[idx] = Endpoint(
                        endpoint.name,
                        endpoint.fields,
                        bool(configured["required"]),
                        bool(configured.get("enabled", endpoint.enabled)),
                    )

    def fetch_stock_master(self) -> pd.DataFrame:
        fields = (
            "ts_code,symbol,name,area,industry,market,exchange,list_status,list_date,delist_date,is_hs,"
            "act_name,act_ent_type"
        )
        frames = []
        for status in ("L", "D", "P", "G"):
            df = self.client.call(
                "stock_basic", fields=fields, required=True, exchange="", list_status=status
            )
            if not df.empty:
                frames.append(df)
        if not frames:
            raise RuntimeError("stock_basic returned no rows for all list statuses")
        master = pd.concat(frames, ignore_index=True).drop_duplicates("ts_code", keep="last")
        master["list_date"] = pd.to_datetime(master["list_date"], errors="coerce")
        master["delist_date"] = pd.to_datetime(master["delist_date"], errors="coerce")
        path = self.settings.paths.metadata / "stock_master.parquet"
        master.to_parquet(path, index=False)
        logger.info("Saved stock master: {} rows -> {}", len(master), path)
        return master

    def fetch_calendar(self, start_date: str, end_date: str) -> pd.DataFrame:
        cal = self.client.call(
            "trade_cal",
            required=True,
            exchange="SSE",
            start_date=start_date,
            end_date=end_date,
            fields="exchange,cal_date,is_open,pretrade_date",
        )
        if cal.empty:
            raise RuntimeError(f"trade_cal returned no rows for {start_date}..{end_date}")
        cal["cal_date"] = pd.to_datetime(cal["cal_date"], errors="raise")
        cal["is_open"] = cal["is_open"].astype(int)
        path = self.settings.paths.metadata / "trade_calendar.parquet"
        cal.to_parquet(path, index=False)
        logger.info("Saved trade calendar: {} rows -> {}", len(cal), path)
        return cal

    def open_dates(self, start_date: str, end_date: str) -> list[str]:
        path = self.settings.paths.metadata / "trade_calendar.parquet"
        cal = pd.read_parquet(path) if path.exists() else self.fetch_calendar(start_date, end_date)
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        available_start = pd.to_datetime(cal["cal_date"]).min()
        available_end = pd.to_datetime(cal["cal_date"]).max()
        if start < available_start or end > available_end:
            cal = self.fetch_calendar(
                min(start, available_start).strftime("%Y%m%d"), max(end, available_end).strftime("%Y%m%d")
            )
        mask = (cal["is_open"] == 1) & (cal["cal_date"] >= start) & (cal["cal_date"] <= end)
        return [str(value) for value in cal.loc[mask, "cal_date"].dt.strftime("%Y%m%d").tolist()]

    def fetch_day(self, trade_date: str, force: bool = False) -> None:
        fetched: dict[str, pd.DataFrame] = {}
        for endpoint in self.endpoints:
            if not endpoint.enabled:
                self.store.write_status(
                    endpoint.name,
                    trade_date,
                    status="disabled",
                    metadata={"api": endpoint.name, "reason": "disabled_by_config"},
                )
                continue
            if not force and self.store.is_terminal(endpoint.name, trade_date):
                fetched[endpoint.name] = self.store.read(endpoint.name, trade_date)
                continue

            params: dict[str, str] = {"trade_date": trade_date}
            if endpoint.name == "suspend_d":
                params["suspend_type"] = "S"
            result = self.client.fetch(
                endpoint.name,
                fields=endpoint.fields,
                required=endpoint.required,
                **params,
            )
            metadata = {
                "api": endpoint.name,
                "trade_date": trade_date,
                "attempts": result.attempts,
                "error": result.error,
                "params": params,
            }
            if result.succeeded:
                if endpoint.required and result.data.empty:
                    raise RuntimeError(f"Required endpoint {endpoint.name} returned empty for {trade_date}")
                self.store.write(endpoint.name, trade_date, result.data, metadata, status=result.status)
                fetched[endpoint.name] = result.data
                logger.info(
                    "{} {}: status={}, rows={}", trade_date, endpoint.name, result.status, len(result.data)
                )
            else:
                self.store.write_status(endpoint.name, trade_date, status=result.status, metadata=metadata)
                logger.warning(
                    "{} {}: status={}, will {}",
                    trade_date,
                    endpoint.name,
                    result.status,
                    "retry" if result.status == "failed" else "skip",
                )

        for required_name in ("daily", "adj_factor", "daily_basic"):
            if required_name not in fetched:
                fetched[required_name] = self.store.read(required_name, trade_date)
        report = validate_raw_day(fetched, trade_date)
        write_report(report, self.settings.paths.quality / "raw" / f"{trade_date}.json")
        assert_quality(report)

    def backfill(self, start_date: str, end_date: str, force: bool = False) -> None:
        if not (self.settings.paths.metadata / "stock_master.parquet").exists():
            self.fetch_stock_master()
        dates = self.open_dates(start_date, end_date)
        source_cfg = self.settings.data.get("data_source", {})
        mysql_cfg = source_cfg.get("mysql", {}) if isinstance(source_cfg, Mapping) else {}
        if self.source_is_mysql and str(mysql_cfg.get("schema", "")).strip().lower() == "lean_canonical_v1":
            preflight = lean_mysql_preflight(mysql_cfg, dates[0], dates[-1]) if dates else {"passed": True}
            if not preflight.get("passed"):
                failures = preflight.get("coverage_failures") or [
                    f"missing_tables:{','.join(preflight.get('missing_tables', []))}"
                ]
                raise RuntimeError(
                    "Lean MySQL source coverage is incomplete for the requested backfill: "
                    f"{failures}. Run source-preflight for details."
                )
            self._backfill_lean_canonical(dates, mysql_cfg, force=force)
            return
        for i, trade_date in enumerate(dates, 1):
            logger.info("Backfill {}/{}: {}", i, len(dates), trade_date)
            self.fetch_day(trade_date, force=force)

    def _backfill_lean_canonical(
        self, dates: list[str], mysql_cfg: Mapping[str, Any], *, force: bool
    ) -> None:
        if not dates:
            return
        optional = self.settings.data["tushare"].get("optional_endpoints", {})
        definitions = build_lean_canonical_range_endpoints(mysql_cfg, optional)
        for endpoint in self.endpoints:
            if not endpoint.enabled:
                for trade_date in dates:
                    self.store.write_status(
                        endpoint.name,
                        trade_date,
                        status="disabled",
                        metadata={"api": endpoint.name, "reason": "disabled_by_config"},
                    )
                continue
            logger.info("Lean MySQL range fetch {}: {}..{}", endpoint.name, dates[0], dates[-1])
            result = self.client.fetch(
                endpoint.name,
                fields=endpoint.fields,
                required=endpoint.required,
                query=str(definitions[endpoint.name]["query"]),
                start_date=dates[0],
                end_date=dates[-1],
            )
            for trade_date in dates:
                if not force and self.store.is_terminal(endpoint.name, trade_date):
                    continue
                if not result.succeeded:
                    self.store.write_status(
                        endpoint.name,
                        trade_date,
                        status=result.status,
                        metadata={"api": endpoint.name, "error": result.error, "range_fetch": True},
                    )
                    continue
                frame = result.data
                if "trade_date" in frame:
                    frame = frame.loc[frame["trade_date"].astype(str) == trade_date].copy()
                else:
                    frame = pd.DataFrame(columns=endpoint.fields.split(","))
                if endpoint.required and frame.empty:
                    raise RuntimeError(f"Required endpoint {endpoint.name} returned empty for {trade_date}")
                status = "empty" if frame.empty else "success"
                self.store.write(
                    endpoint.name,
                    trade_date,
                    frame,
                    {
                        "api": endpoint.name,
                        "trade_date": trade_date,
                        "attempts": result.attempts,
                        "params": {"start_date": dates[0], "end_date": dates[-1]},
                        "range_fetch": True,
                    },
                    status=status,
                )
            del result

        for position, trade_date in enumerate(dates, 1):
            logger.info("Lean MySQL validate partition {}/{}: {}", position, len(dates), trade_date)
            fetched = {
                required_name: self.store.read(required_name, trade_date)
                for required_name in ("daily", "adj_factor", "daily_basic")
            }
            report = validate_raw_day(fetched, trade_date)
            write_report(report, self.settings.paths.quality / "raw" / f"{trade_date}.json")
            assert_quality(report)

    def source_preflight(self, start_date: str, end_date: str) -> dict[str, Any]:
        if not self.source_is_mysql:
            raise ValueError("source-preflight currently requires data_source.kind=lean_mysql")
        source_cfg = self.settings.data.get("data_source", {})
        mysql_cfg = source_cfg.get("mysql") if isinstance(source_cfg, Mapping) else None
        if not isinstance(mysql_cfg, Mapping):
            raise ValueError("data_source.mysql configuration is required")
        return lean_mysql_preflight(mysql_cfg, start_date, end_date)

    def sync_benchmark(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        normalized_symbol = symbol.strip().upper()
        if self.source_is_mysql:
            source_cfg = self.settings.data.get("data_source", {})
            mysql_cfg = source_cfg.get("mysql") if isinstance(source_cfg, Mapping) else None
            if not isinstance(mysql_cfg, Mapping):
                raise ValueError("sync-benchmark requires data_source.mysql configuration")
            frame = fetch_lean_benchmark(mysql_cfg, normalized_symbol, start_date, end_date)
        else:
            if (
                len(normalized_symbol) == 8
                and normalized_symbol[:2] in {"SH", "SZ", "BJ"}
                and normalized_symbol[2:].isdigit()
            ):
                ts_code = f"{normalized_symbol[2:]}.{normalized_symbol[:2]}"
            elif (
                len(normalized_symbol) == 9
                and normalized_symbol[:6].isdigit()
                and normalized_symbol[6:] in {".SH", ".SZ", ".BJ"}
            ):
                ts_code = normalized_symbol
            else:
                raise ValueError(f"unsupported benchmark symbol: {symbol}")
            frame = self.client.call(
                "index_daily",
                required=True,
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                fields=INDEX_DAILY_FIELDS,
            )
        if frame.empty:
            raise RuntimeError(
                f"benchmark source has no index {normalized_symbol} for {start_date}..{end_date}"
            )
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise")
        if frame["trade_date"].duplicated().any():
            raise ValueError(f"duplicate benchmark dates for {symbol}; configure one canonical source")
        frame = frame.sort_values("trade_date").reset_index(drop=True)
        target = self.settings.paths.metadata / "benchmarks" / f"{normalized_symbol}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            existing = pd.read_parquet(target)
            frame = (
                pd.concat([existing, frame], ignore_index=True)
                .sort_values("trade_date")
                .drop_duplicates("trade_date", keep="last")
                .reset_index(drop=True)
            )
        temporary = target.with_suffix(".parquet.tmp")
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, target)
        logger.info("Saved benchmark {}: {} rows -> {}", normalized_symbol, len(frame), target)
        return frame

    def sync_universe_membership(self, start_date: str, end_date: str) -> pd.DataFrame:
        configured = configured_universe(self.settings)
        if configured is None:
            raise ValueError("sync-universe requires a named point-in-time universe")
        _, index_code, _ = configured
        universe_cfg = self.settings.data.get("universe", {})
        lag = int(universe_cfg.get("membership_effective_lag_days", 1))
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        if start > end:
            raise ValueError("universe start_date must not be after end_date")
        frames: list[pd.DataFrame] = []
        source_intervals: pd.DataFrame | None = None
        if self.source_is_mysql:
            source_cfg = self.settings.data.get("data_source", {})
            mysql_cfg = source_cfg.get("mysql", {}) if isinstance(source_cfg, Mapping) else {}
            universe_code = str(mysql_cfg.get("universe", index_code))
            if str(mysql_cfg.get("schema", "")).strip().lower() == "lean_canonical_v1":
                source_intervals = fetch_lean_universe_intervals(
                    mysql_cfg,
                    universe_code,
                    start.strftime("%Y%m%d"),
                    end.strftime("%Y%m%d"),
                )
            else:
                frame = self.client.call(
                    "index_weight",
                    required=True,
                    index_code=universe_code,
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                )
                frames.append(frame)
        else:
            # Query month-by-month: index_weight is snapshot-oriented and large ranges
            # may be truncated by the upstream service.
            for period in pd.period_range(start=start, end=end, freq="M"):
                month_start = max(start, period.start_time)
                month_end = min(end, period.end_time)
                frame = self.client.call(
                    "index_weight",
                    required=True,
                    index_code=index_code,
                    start_date=month_start.strftime("%Y%m%d"),
                    end_date=month_end.strftime("%Y%m%d"),
                    fields="index_code,con_code,trade_date,weight",
                )
                if not frame.empty:
                    frames.append(frame)
        calendar_path = self.settings.paths.metadata / "trade_calendar.parquet"
        if not calendar_path.is_file():
            self.fetch_calendar(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
        calendar_frame = pd.read_parquet(calendar_path)
        calendar = pd.DatetimeIndex(
            pd.to_datetime(
                calendar_frame.loc[calendar_frame["is_open"].astype(int) == 1, "cal_date"],
                errors="coerce",
            ).dropna()
        )
        if source_intervals is not None:
            source_path = (
                self.settings.paths.metadata / "universe_source_intervals" / f"{configured[0]}.parquet"
            )
            source_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = source_path.with_suffix(".parquet.tmp")
            source_intervals.to_parquet(temporary, index=False)
            os.replace(temporary, source_path)
            intervals = build_membership_from_source_intervals(
                source_intervals,
                calendar,
                universe_code=index_code,
                effective_lag_days=lag,
            )
        else:
            snapshots = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            snapshot_path = self.settings.paths.metadata / "universe_snapshots" / f"{configured[0]}.parquet"
            if snapshot_path.is_file():
                snapshots = pd.concat([pd.read_parquet(snapshot_path), snapshots], ignore_index=True)
            if not snapshots.empty:
                snapshots = (
                    snapshots.sort_values(["trade_date", "con_code"])
                    .drop_duplicates(["trade_date", "con_code"], keep="last")
                    .reset_index(drop=True)
                )
                snapshot_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = snapshot_path.with_suffix(".parquet.tmp")
                snapshots.to_parquet(temporary, index=False)
                os.replace(temporary, snapshot_path)
            intervals = build_membership_intervals(
                snapshots,
                calendar,
                universe_code=index_code,
                effective_lag_days=lag,
            )
        path = write_membership(self.settings, intervals)
        logger.info("Saved PIT universe {}: {} intervals -> {}", index_code, len(intervals), path)
        return intervals
