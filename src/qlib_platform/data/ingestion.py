from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd
from loguru import logger

from qlib_platform.data._legacy_ingestion import (
    ADJ_FIELDS,
    BASIC_FIELDS,
    DAILY_FIELDS,
    LIMIT_FIELDS,
    MONEYFLOW_FIELDS,
    ST_FIELDS,
    SUSPEND_FIELDS,
    Endpoint,
    Extractor as _LegacyExtractor,
)
from qlib_platform.data.quality import assert_quality, validate_raw_day, write_report
from qlib_platform.data.store import PartitionStore
from qlib_platform.data.sources import RetryPolicy, create_data_source
from qlib_platform.data.sources.mysql import build_lean_canonical_range_endpoints


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _source_runtime_config(settings: Any) -> Mapping[str, Any]:
    source_cfg = _mapping(settings.data.get("data_source"))
    runtime = source_cfg.get("runtime")
    if isinstance(runtime, Mapping):
        return runtime
    # Backward compatibility for existing pipeline YAMLs. New configs should
    # move retry/endpoint knobs under ``data_source`` rather than a vendor block.
    return _mapping(settings.data.get("tushare"))


def _optional_endpoints(settings: Any) -> Mapping[str, Any]:
    source_cfg = _mapping(settings.data.get("data_source"))
    value = source_cfg.get("optional_endpoints")
    if isinstance(value, Mapping):
        return value
    legacy = _mapping(settings.data.get("tushare"))
    value = legacy.get("optional_endpoints")
    return value if isinstance(value, Mapping) else {}


class Extractor(_LegacyExtractor):
    """Provider-neutral ingestion orchestrator.

    The extraction behavior remains compatible with the certified pipeline, but
    source construction is delegated to the adapter registry. New providers
    implement the normalized client contract and register a factory; this class
    does not gain another provider-specific constructor branch.
    """

    def __init__(self, settings: Any):
        runtime = _source_runtime_config(settings)
        retry_policy = RetryPolicy(
            int(runtime.get("max_attempts", 6)),
            float(runtime.get("base_sleep_seconds", 2.0)),
            float(runtime.get("max_sleep_seconds", 60.0)),
            float(runtime.get("jitter_ratio", 0.15)),
        )
        binding = create_data_source(settings, retry_policy)

        self.settings = settings
        self.store = PartitionStore(settings.paths.raw)
        self.data_source = binding
        self.client = binding.client
        # Only retained for inherited Lean/MySQL optimized range paths. Generic
        # provider selection itself is handled by the registry above.
        self.source_is_mysql = "mysql" in binding.capabilities

        optional = _optional_endpoints(settings)
        endpoints = [
            Endpoint("daily", DAILY_FIELDS, True, enabled=bool(optional.get("daily", True))),
            Endpoint("adj_factor", ADJ_FIELDS, True, enabled=bool(optional.get("adj_factor", True))),
            Endpoint("daily_basic", BASIC_FIELDS, True, enabled=bool(optional.get("daily_basic", True))),
            Endpoint("moneyflow", MONEYFLOW_FIELDS, False, enabled=bool(optional.get("moneyflow", True))),
            Endpoint("stk_limit", LIMIT_FIELDS, False, enabled=bool(optional.get("stk_limit", True))),
            Endpoint("suspend_d", SUSPEND_FIELDS, False, enabled=bool(optional.get("suspend_d", True))),
            Endpoint("stock_st", ST_FIELDS, False, enabled=bool(optional.get("stock_st", True))),
        ]
        self.endpoints = []
        for endpoint in endpoints:
            override = binding.endpoint_overrides.get(endpoint.name)
            if override is None:
                self.endpoints.append(endpoint)
                continue
            self.endpoints.append(
                Endpoint(
                    endpoint.name,
                    endpoint.fields,
                    endpoint.required if override.required is None else override.required,
                    endpoint.enabled if override.enabled is None else override.enabled,
                )
            )

    def open_dates(self, start_date: str, end_date: str) -> list[str]:
        """Return open market dates in the canonical ``YYYYMMDD`` partition format."""

        path = self.settings.paths.metadata / "trade_calendar.parquet"
        if not path.exists():
            self.fetch_calendar(start_date, end_date)
        frame = pd.read_parquet(path)
        calendar_dates = pd.to_datetime(frame["cal_date"], errors="raise").dt.normalize()
        start = pd.Timestamp(start_date).normalize()
        end = pd.Timestamp(end_date).normalize()
        mask = calendar_dates.between(start, end) & frame["is_open"].astype(int).eq(1)
        return sorted(calendar_dates.loc[mask].dt.strftime("%Y%m%d").unique().tolist())

    def _backfill_lean_canonical(
        self,
        dates: list[str],
        mysql_cfg: Mapping[str, Any],
        *,
        force: bool,
    ) -> None:
        """Range-fetch Lean/MySQL without depending on any Tushare configuration."""

        if not dates:
            return
        definitions = build_lean_canonical_range_endpoints(mysql_cfg, _optional_endpoints(self.settings))
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
