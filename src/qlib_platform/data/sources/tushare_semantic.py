from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from qlib_platform.data.sources.base import DataSourceClient, FetchResult
from qlib_platform.data.sources.semantic import (
    CanonicalBatch,
    DatasetCapability,
    DatasetRequest,
    FetchEnvelope,
    SourceCapabilities,
    canonical_coverage,
    frame_sha256,
    validate_canonical_batch,
)
from qlib_platform.data.symbols import ts_to_qlib

_DAILY_FIELDS = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
_BASIC_FIELDS = (
    "ts_code,trade_date,close,turnover_rate,turnover_rate_f,volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,"
    "dv_ratio,dv_ttm,total_share,float_share,free_share,total_mv,circ_mv,limit_status"
)
_ADJ_FIELDS = "ts_code,trade_date,adj_factor"
_MASTER_FIELDS = (
    "ts_code,symbol,name,area,industry,market,exchange,list_status,list_date,delist_date,is_hs,"
    "act_name,act_ent_type"
)
_CALENDAR_FIELDS = "exchange,cal_date,is_open,pretrade_date"


class TushareSemanticDataSource:
    """Provider-neutral semantic adapter over the existing TuShare transport client."""

    def __init__(self, client: DataSourceClient) -> None:
        self._client = client

    @property
    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            provider="tushare",
            datasets=(
                DatasetCapability("equity_daily"),
                DatasetCapability("equity_daily_basic"),
                DatasetCapability("adjustment_factor"),
                DatasetCapability("instrument_master"),
                DatasetCapability("trading_calendar"),
            ),
        )

    def fetch_dataset(self, request: DatasetRequest) -> FetchEnvelope:
        try:
            self.capabilities.negotiate(request)
        except Exception as exc:
            return FetchEnvelope("tushare", "unsupported", 1, error_class="unsupported", error=str(exc))
        handlers: dict[str, Callable[[DatasetRequest], FetchEnvelope]] = {
            "equity_daily": self._fetch_daily,
            "equity_daily_basic": self._fetch_daily_basic,
            "adjustment_factor": self._fetch_adjustment_factor,
            "instrument_master": self._fetch_instrument_master,
            "trading_calendar": self._fetch_calendar,
        }
        return handlers[request.dataset_kind](request)

    def _fetch_daily(self, request: DatasetRequest) -> FetchEnvelope:
        result = self._client.fetch(
            "daily",
            fields=_DAILY_FIELDS,
            required=False,
            **_date_params(request),
        )
        if result.status != "success":
            return _failure_envelope(result, request)
        required = {"ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"}
        missing = required - set(result.data.columns)
        if missing:
            return _schema_failure(result, missing)
        raw = result.data.copy()
        frame = pd.DataFrame(
            {
                "instrument": raw["ts_code"].astype(str).map(ts_to_qlib),
                "trading_date": pd.to_datetime(
                    raw["trade_date"], format="%Y%m%d", errors="raise"
                ).dt.strftime("%Y-%m-%d"),
                "event_time": _ashare_close_time(raw["trade_date"]),
                "available_at": pd.NaT,
                "open": pd.to_numeric(raw["open"], errors="raise"),
                "high": pd.to_numeric(raw["high"], errors="raise"),
                "low": pd.to_numeric(raw["low"], errors="raise"),
                "close": pd.to_numeric(raw["close"], errors="raise"),
                "volume": pd.to_numeric(raw["vol"], errors="raise") * 100.0,
                "turnover": pd.to_numeric(raw["amount"], errors="raise") * 1000.0,
            }
        )
        return _success_envelope(
            request,
            raw,
            _select_fields(frame, request),
            source_units={"vol": "hand", "amount": "CNY_thousand"},
            canonical_units={
                "open": "CNY/share",
                "high": "CNY/share",
                "low": "CNY/share",
                "close": "CNY/share",
                "volume": "share",
                "turnover": "CNY",
            },
            attempts=result.attempts,
        )

    def _fetch_daily_basic(self, request: DatasetRequest) -> FetchEnvelope:
        result = self._client.fetch(
            "daily_basic",
            fields=_BASIC_FIELDS,
            required=False,
            **_date_params(request),
        )
        if result.status != "success":
            return _failure_envelope(result, request)
        required = {
            "ts_code",
            "trade_date",
            "total_share",
            "float_share",
            "free_share",
            "total_mv",
            "circ_mv",
        }
        missing = required - set(result.data.columns)
        if missing:
            return _schema_failure(result, missing)
        raw = result.data.copy()
        frame = pd.DataFrame(
            {
                "instrument": raw["ts_code"].astype(str).map(ts_to_qlib),
                "trading_date": pd.to_datetime(
                    raw["trade_date"], format="%Y%m%d", errors="raise"
                ).dt.strftime("%Y-%m-%d"),
                "event_time": _ashare_close_time(raw["trade_date"]),
                "available_at": pd.NaT,
                "total_shares": pd.to_numeric(raw["total_share"], errors="coerce") * 10000.0,
                "float_shares": pd.to_numeric(raw["float_share"], errors="coerce") * 10000.0,
                "free_shares": pd.to_numeric(raw["free_share"], errors="coerce") * 10000.0,
                "total_market_value": pd.to_numeric(raw["total_mv"], errors="coerce") * 10000.0,
                "circulating_market_value": pd.to_numeric(raw["circ_mv"], errors="coerce") * 10000.0,
            }
        )
        for column in ("turnover_rate", "turnover_rate_f", "dv_ratio", "dv_ttm"):
            if column in raw.columns:
                frame[column] = pd.to_numeric(raw[column], errors="coerce") / 100.0
        for column in ("volume_ratio", "pe", "pe_ttm", "pb", "ps", "ps_ttm", "limit_status"):
            if column in raw.columns:
                frame[column] = pd.to_numeric(raw[column], errors="coerce")
        return _success_envelope(
            request,
            raw,
            _select_fields(frame, request),
            source_units={"total_share": "10k_share", "total_mv": "CNY_10k", "turnover_rate": "percent"},
            canonical_units={
                "total_shares": "share",
                "float_shares": "share",
                "free_shares": "share",
                "total_market_value": "CNY",
                "circulating_market_value": "CNY",
                "turnover_rate": "ratio",
            },
            attempts=result.attempts,
        )

    def _fetch_adjustment_factor(self, request: DatasetRequest) -> FetchEnvelope:
        result = self._client.fetch(
            "adj_factor",
            fields=_ADJ_FIELDS,
            required=False,
            **_date_params(request),
        )
        if result.status != "success":
            return _failure_envelope(result, request)
        required = {"ts_code", "trade_date", "adj_factor"}
        missing = required - set(result.data.columns)
        if missing:
            return _schema_failure(result, missing)
        raw = result.data.copy()
        frame = pd.DataFrame(
            {
                "instrument": raw["ts_code"].astype(str).map(ts_to_qlib),
                "trading_date": pd.to_datetime(
                    raw["trade_date"], format="%Y%m%d", errors="raise"
                ).dt.strftime("%Y-%m-%d"),
                "event_time": _ashare_close_time(raw["trade_date"]),
                "available_at": pd.NaT,
                "adjustment_factor": pd.to_numeric(raw["adj_factor"], errors="raise"),
            }
        )
        return _success_envelope(
            request,
            raw,
            _select_fields(frame, request),
            source_units={"adj_factor": "dimensionless"},
            canonical_units={"adjustment_factor": "dimensionless"},
            attempts=result.attempts,
        )

    def _fetch_instrument_master(self, request: DatasetRequest) -> FetchEnvelope:
        frames: list[pd.DataFrame] = []
        attempts = 0
        for list_status in ("L", "D"):
            result = self._client.fetch(
                "stock_basic",
                fields=_MASTER_FIELDS,
                required=False,
                list_status=list_status,
            )
            attempts += result.attempts
            if result.status not in {"success", "empty"}:
                return _failure_envelope(result, request)
            frames.append(result.data)
        raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        required = {"ts_code", "list_status", "list_date", "delist_date"}
        missing = required - set(raw.columns)
        if missing:
            return _schema_failure(FetchResult(raw, "success", max(attempts, 1)), missing)
        raw = raw.drop_duplicates("ts_code").sort_values("ts_code").reset_index(drop=True)
        frame = raw.rename(columns={"list_status": "listing_status"}).copy()
        frame.insert(0, "instrument", raw["ts_code"].astype(str).map(ts_to_qlib))
        frame["event_time"] = pd.Timestamp("1970-01-01", tz="UTC")
        frame["available_at"] = pd.NaT
        frame = frame.drop(columns=["ts_code"], errors="ignore")
        return _success_envelope(
            request,
            raw,
            _select_fields(frame, request),
            source_units={},
            canonical_units={},
            attempts=max(attempts, 1),
            timezone="UTC",
        )

    def _fetch_calendar(self, request: DatasetRequest) -> FetchEnvelope:
        result = self._client.fetch(
            "trade_cal",
            fields=_CALENDAR_FIELDS,
            required=False,
            exchange="",
            **_calendar_date_params(request),
        )
        if result.status != "success":
            return _failure_envelope(result, request)
        required = {"cal_date", "is_open"}
        missing = required - set(result.data.columns)
        if missing:
            return _schema_failure(result, missing)
        raw = result.data.copy()
        dates = pd.to_datetime(raw["cal_date"], format="%Y%m%d", errors="raise")
        frame = pd.DataFrame(
            {
                "trading_date": dates.dt.strftime("%Y-%m-%d"),
                "event_time": (dates + pd.Timedelta(hours=15)).dt.tz_localize("Asia/Shanghai"),
                "available_at": pd.NaT,
                "exchange": raw.get("exchange", ""),
                "is_open": pd.to_numeric(raw["is_open"], errors="raise").astype(bool),
                "previous_trading_date": pd.to_datetime(
                    raw.get("pretrade_date", pd.Series(index=raw.index, dtype=str)),
                    format="%Y%m%d",
                    errors="coerce",
                ).dt.strftime("%Y-%m-%d"),
            }
        )
        return _success_envelope(
            request,
            raw,
            _select_fields(frame, request),
            source_units={},
            canonical_units={},
            attempts=result.attempts,
        )


def _date_params(request: DatasetRequest) -> dict[str, str]:
    if (
        request.start
        and request.end
        and pd.Timestamp(request.start).date() == pd.Timestamp(request.end).date()
    ):
        return {"trade_date": pd.Timestamp(request.start).strftime("%Y%m%d")}
    params: dict[str, str] = {}
    if request.start:
        params["start_date"] = pd.Timestamp(request.start).strftime("%Y%m%d")
    if request.end:
        params["end_date"] = pd.Timestamp(request.end).strftime("%Y%m%d")
    return params


def _calendar_date_params(request: DatasetRequest) -> dict[str, str]:
    params: dict[str, str] = {}
    if request.start:
        params["start_date"] = pd.Timestamp(request.start).strftime("%Y%m%d")
    if request.end:
        params["end_date"] = pd.Timestamp(request.end).strftime("%Y%m%d")
    return params


def _ashare_close_time(values: pd.Series) -> pd.Series:
    dates = pd.to_datetime(values, format="%Y%m%d", errors="raise")
    return (dates + pd.Timedelta(hours=15)).dt.tz_localize("Asia/Shanghai")


def _select_fields(frame: pd.DataFrame, request: DatasetRequest) -> pd.DataFrame:
    if not request.fields:
        return frame.reset_index(drop=True)
    identity = [
        column for column in ("instrument", "trading_date", "event_time", "available_at") if column in frame
    ]
    selected = list(dict.fromkeys([*identity, *request.fields]))
    if set(selected) - set(frame.columns):
        return frame.reset_index(drop=True)
    return frame[selected].reset_index(drop=True)


def _success_envelope(
    request: DatasetRequest,
    raw: pd.DataFrame,
    frame: pd.DataFrame,
    *,
    source_units: dict[str, str],
    canonical_units: dict[str, str],
    attempts: int,
    timezone: str = "Asia/Shanghai",
) -> FetchEnvelope:
    coverage = canonical_coverage(frame, event_time_column="event_time", request=request)
    batch = CanonicalBatch(
        dataset_kind=request.dataset_kind,
        data=frame,
        schema_version=request.schema_version,
        timezone=timezone,
        source_units=source_units,
        canonical_units=canonical_units,
        coverage=coverage,
    )
    failure = validate_canonical_batch(batch, request)
    return FetchEnvelope(
        "tushare",
        failure or "success",
        attempts,
        batch=batch,
        provider_revision="tushare-pro",
        source_hash=frame_sha256(raw),
        entitlement="granted",
        error_class=failure,
    )


def _failure_envelope(result: FetchResult, request: DatasetRequest) -> FetchEnvelope:
    del request
    if result.status == "permission_denied":
        status = "permission_denied"
    elif result.status == "empty":
        status = "incomplete"
    else:
        text = (result.error or "").lower()
        if any(token in text for token in ("429", "rate limit", "频率")):
            status = "rate_limited"
        elif any(token in text for token in ("timeout", "timed out", "超时")):
            status = "timeout"
        else:
            status = "provider_error"
    return FetchEnvelope(
        "tushare",
        status,
        result.attempts,
        source_hash=frame_sha256(result.data),
        entitlement="denied" if status == "permission_denied" else "unknown",
        error_class=status,
        error=result.error,
    )


def _schema_failure(result: FetchResult, missing: set[str]) -> FetchEnvelope:
    return FetchEnvelope(
        "tushare",
        "schema_mismatch",
        result.attempts,
        source_hash=frame_sha256(result.data),
        entitlement="granted",
        error_class="missing_columns",
        error=f"missing provider columns: {sorted(missing)}",
    )
