from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import pandas as pd

from qlib_platform.data.sources.semantic import (
    CanonicalBatch,
    DataSourceContractError,
    DatasetRequest,
    FetchEnvelope,
    PaginationCursor,
    PaginationEvidence,
    PaginationPolicy,
    SemanticDataSource,
    canonical_coverage,
    request_fingerprint,
    validate_canonical_batch,
)
from qlib_platform.data.store import PartitionStore
from qlib_platform.data.symbols import qlib_to_ts

SEMANTIC_RAW_DATASETS: Mapping[str, str] = {
    "daily": "equity_daily",
    "daily_basic": "equity_daily_basic",
    "adj_factor": "adjustment_factor",
}


def semantic_request_for_day(dataset_kind: str, trade_date: str) -> DatasetRequest:
    date = pd.Timestamp(trade_date).strftime("%Y-%m-%d")
    return DatasetRequest(dataset_kind=dataset_kind, start=date, end=date, require_complete=True)


def consume_semantic_partition(
    source: SemanticDataSource,
    store: PartitionStore,
    request: DatasetRequest,
    partition_key: str,
    *,
    policy: PaginationPolicy | None = None,
) -> FetchEnvelope:
    """Fetch one semantic partition with durable, fail-closed pagination resume.

    A provider cursor is never treated as durable state by itself. Accepted
    canonical pages are written beside the cursor, and a resumed tail is merged
    with that durable prefix and fully revalidated before the partition can be
    promoted to ``success``.
    """

    resolved_policy = policy or PaginationPolicy()
    fingerprint = request_fingerprint(request)
    prefix, resume_cursor, previous = _load_resume_prefix(store, request, partition_key, fingerprint)

    paginated_fetch = getattr(source, "fetch_dataset_paginated", None)
    if resume_cursor is not None and not callable(paginated_fetch):
        raise DataSourceContractError(
            f"semantic source {source.capabilities.provider!r} cannot resume stored pagination state"
        )
    if callable(paginated_fetch):
        envelope = paginated_fetch(request, policy=resolved_policy, cursor=resume_cursor)
    else:
        envelope = source.fetch_dataset(request)

    combined_batch = _combine_batches(prefix, envelope.batch, request)
    attempts_total = int(previous.get("attempts_total", 0)) + envelope.attempts
    page_count_total = int(previous.get("page_count_total", 0)) + (
        0 if envelope.pagination is None else envelope.pagination.page_count
    )
    source_hash = _combine_hashes(
        str(previous.get("source_hash", "")) or None,
        envelope.source_hash,
    )

    continuity_failure = _resume_continuity_failure(previous, envelope)
    validation_failure = (
        validate_canonical_batch(combined_batch, request) if combined_batch is not None else "incomplete"
    )
    terminal = envelope.pagination is None or envelope.pagination.terminal
    recoverable_tail = envelope.status == "success" or (
        envelope.status == "incomplete"
        and envelope.error_class in {None, "incomplete", "resume_prefix_required"}
    )

    if continuity_failure is None and terminal and recoverable_tail and validation_failure is None:
        assert combined_batch is not None
        evidence = _completed_evidence(
            envelope.pagination,
            fingerprint=fingerprint,
            page_count_total=page_count_total,
            row_count=len(combined_batch.data),
            page_size=resolved_policy.page_size,
        )
        completed = FetchEnvelope(
            provider=envelope.provider,
            status="success",
            attempts=max(attempts_total, 1),
            batch=combined_batch,
            provider_revision=envelope.provider_revision or _optional_str(previous.get("provider_revision")),
            source_hash=source_hash,
            entitlement=envelope.entitlement,
            pagination=evidence,
        )
        store.write(
            request.dataset_kind,
            partition_key,
            combined_batch.data,
            _manifest_metadata(
                request,
                completed,
                fingerprint=fingerprint,
                attempts_total=attempts_total,
                page_count_total=page_count_total,
            ),
            status="success",
        )
        return completed

    status = "conflict" if continuity_failure is not None else envelope.status
    error_class = continuity_failure or envelope.error_class or validation_failure
    error = (
        "semantic pagination resume continuity changed across durable segments"
        if continuity_failure is not None
        else envelope.error
    )
    failed = FetchEnvelope(
        provider=envelope.provider,
        status=status,
        attempts=max(attempts_total, 1),
        batch=combined_batch,
        provider_revision=envelope.provider_revision or _optional_str(previous.get("provider_revision")),
        source_hash=source_hash,
        entitlement=envelope.entitlement,
        error_class=error_class,
        error=error,
        pagination=envelope.pagination,
    )
    metadata = _manifest_metadata(
        request,
        failed,
        fingerprint=fingerprint,
        attempts_total=attempts_total,
        page_count_total=page_count_total,
    )
    if combined_batch is None:
        store.write_status(request.dataset_kind, partition_key, status=status, metadata=metadata)
    else:
        store.write(
            request.dataset_kind,
            partition_key,
            combined_batch.data,
            metadata,
            status=status,
        )
    return failed


def legacy_projection(batch: CanonicalBatch) -> pd.DataFrame:
    """Project canonical values into the frozen historical raw-consumer schema."""

    handlers = {
        "equity_daily": _project_daily,
        "equity_daily_basic": _project_daily_basic,
        "adjustment_factor": _project_adjustment_factor,
        "instrument_master": _project_instrument_master,
        "trading_calendar": _project_trading_calendar,
    }
    handler = handlers.get(batch.dataset_kind)
    if handler is None:
        raise DataSourceContractError(
            f"no legacy compatibility projection for semantic dataset {batch.dataset_kind!r}"
        )
    return handler(batch.data)


def _load_resume_prefix(
    store: PartitionStore,
    request: DatasetRequest,
    partition_key: str,
    fingerprint: str,
) -> tuple[CanonicalBatch | None, PaginationCursor | None, dict[str, Any]]:
    manifest = store.read_manifest(request.dataset_kind, partition_key)
    if not manifest or manifest.get("status") == "success":
        return None, None, {}

    pagination = manifest.get("pagination")
    if not isinstance(pagination, Mapping):
        return None, None, {}
    cursor_payload = pagination.get("next_cursor")
    if not isinstance(cursor_payload, Mapping):
        return None, None, {}

    stored_fingerprint = str(manifest.get("request_fingerprint", ""))
    if stored_fingerprint != fingerprint:
        raise DataSourceContractError(
            "durable semantic pagination prefix belongs to a different request fingerprint"
        )
    cursor = PaginationCursor(
        provider=str(cursor_payload.get("provider", "")),
        dataset_kind=str(cursor_payload.get("dataset_kind", "")),
        request_fingerprint=str(cursor_payload.get("request_fingerprint", "")),
        next_offset=int(cursor_payload.get("next_offset", -1)),
    )
    if not store.exists(request.dataset_kind, partition_key):
        raise DataSourceContractError("pagination cursor exists without its durable canonical prefix")

    frame = store.read(request.dataset_kind, partition_key)
    batch = CanonicalBatch(
        dataset_kind=request.dataset_kind,
        data=frame,
        schema_version=str(manifest.get("schema_version", request.schema_version)),
        timezone=str(manifest.get("timezone", "UTC")),
        source_units=_string_mapping(manifest.get("source_units")),
        canonical_units=_string_mapping(manifest.get("canonical_units")),
        event_time_column=str(manifest.get("event_time_column", "event_time")),
        available_at_column=_optional_str(manifest.get("available_at_column")),
        coverage=canonical_coverage(
            frame,
            event_time_column=str(manifest.get("event_time_column", "event_time")),
            request=request,
        ),
    )
    return batch, cursor, manifest


def _combine_batches(
    prefix: CanonicalBatch | None,
    tail: CanonicalBatch | None,
    request: DatasetRequest,
) -> CanonicalBatch | None:
    if prefix is None:
        return tail
    if tail is None:
        return replace(
            prefix,
            coverage=canonical_coverage(
                prefix.data,
                event_time_column=prefix.event_time_column,
                request=request,
            ),
        )
    contract_fields = (
        (prefix.dataset_kind, tail.dataset_kind, "dataset_kind"),
        (prefix.schema_version, tail.schema_version, "schema_version"),
        (prefix.timezone, tail.timezone, "timezone"),
        (prefix.event_time_column, tail.event_time_column, "event_time_column"),
        (dict(prefix.source_units), dict(tail.source_units), "source_units"),
        (dict(prefix.canonical_units), dict(tail.canonical_units), "canonical_units"),
    )
    for expected, actual, field_name in contract_fields:
        if expected != actual:
            raise DataSourceContractError(
                f"durable pagination {field_name} changed between prefix and resumed tail"
            )
    frame = pd.concat([prefix.data, tail.data], ignore_index=True)
    return replace(
        tail,
        data=frame,
        coverage=canonical_coverage(
            frame,
            event_time_column=tail.event_time_column,
            request=request,
        ),
    )


def _resume_continuity_failure(previous: Mapping[str, Any], envelope: FetchEnvelope) -> str | None:
    if not previous:
        return None
    provider = _optional_str(previous.get("provider"))
    if provider is not None and provider != envelope.provider:
        return "provider_mismatch"
    revision = _optional_str(previous.get("provider_revision"))
    if revision is not None and envelope.provider_revision not in {None, revision}:
        return "provider_revision_drift"
    return None


def _completed_evidence(
    evidence: PaginationEvidence | None,
    *,
    fingerprint: str,
    page_count_total: int,
    row_count: int,
    page_size: int,
) -> PaginationEvidence | None:
    if evidence is None:
        return None
    return PaginationEvidence(
        request_fingerprint=fingerprint,
        start_offset=0,
        page_size=page_size,
        page_count=page_count_total,
        row_count=row_count,
        terminal=True,
        next_cursor=None,
    )


def _manifest_metadata(
    request: DatasetRequest,
    envelope: FetchEnvelope,
    *,
    fingerprint: str,
    attempts_total: int,
    page_count_total: int,
) -> dict[str, Any]:
    batch = envelope.batch
    return {
        "semantic_contract": "canonical_batch_v1",
        "dataset_kind": request.dataset_kind,
        "provider": envelope.provider,
        "provider_revision": envelope.provider_revision,
        "source_hash": envelope.source_hash,
        "entitlement": envelope.entitlement,
        "request_fingerprint": fingerprint,
        "schema_version": request.schema_version if batch is None else batch.schema_version,
        "timezone": None if batch is None else batch.timezone,
        "source_units": {} if batch is None else dict(batch.source_units),
        "canonical_units": {} if batch is None else dict(batch.canonical_units),
        "event_time_column": "event_time" if batch is None else batch.event_time_column,
        "available_at_column": None if batch is None else batch.available_at_column,
        "attempts_total": attempts_total,
        "page_count_total": page_count_total,
        "error_class": envelope.error_class,
        "error": envelope.error,
        "pagination": _pagination_payload(envelope.pagination),
    }


def _pagination_payload(evidence: PaginationEvidence | None) -> dict[str, Any] | None:
    if evidence is None:
        return None
    cursor = evidence.next_cursor
    return {
        "request_fingerprint": evidence.request_fingerprint,
        "start_offset": evidence.start_offset,
        "page_size": evidence.page_size,
        "page_count": evidence.page_count,
        "row_count": evidence.row_count,
        "terminal": evidence.terminal,
        "next_cursor": (
            None
            if cursor is None
            else {
                "provider": cursor.provider,
                "dataset_kind": cursor.dataset_kind,
                "request_fingerprint": cursor.request_fingerprint,
                "next_offset": cursor.next_offset,
            }
        ),
    }


def _combine_hashes(left: str | None, right: str | None) -> str | None:
    values = [value for value in (left, right) if value]
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    digest = hashlib.sha256()
    for value in values:
        digest.update(value.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _identity(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"instrument", "trading_date"}
    missing = required - set(frame.columns)
    if missing:
        raise DataSourceContractError(f"canonical compatibility projection missing columns: {sorted(missing)}")
    return pd.DataFrame(
        {
            "ts_code": frame["instrument"].astype(str).map(qlib_to_ts),
            "trade_date": pd.to_datetime(frame["trading_date"], errors="raise").dt.strftime("%Y%m%d"),
        }
    )


def _project_daily(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "open",
        "high",
        "low",
        "close",
        "previous_close",
        "absolute_change",
        "return_ratio",
        "volume",
        "turnover",
    }
    missing = required - set(frame.columns)
    if missing:
        raise DataSourceContractError(f"canonical daily projection missing columns: {sorted(missing)}")
    result = _identity(frame)
    result["open"] = frame["open"]
    result["high"] = frame["high"]
    result["low"] = frame["low"]
    result["close"] = frame["close"]
    result["pre_close"] = frame["previous_close"]
    result["change"] = frame["absolute_change"]
    result["pct_chg"] = pd.to_numeric(frame["return_ratio"], errors="raise") * 100.0
    result["vol"] = pd.to_numeric(frame["volume"], errors="raise") / 100.0
    result["amount"] = pd.to_numeric(frame["turnover"], errors="raise") / 1000.0
    return result


def _project_daily_basic(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "total_shares",
        "float_shares",
        "free_shares",
        "total_market_value",
        "circulating_market_value",
    }
    missing = required - set(frame.columns)
    if missing:
        raise DataSourceContractError(f"canonical daily-basic projection missing columns: {sorted(missing)}")
    result = _identity(frame)
    if "close" in frame:
        result["close"] = frame["close"]
    for column in ("turnover_rate", "turnover_rate_f", "dv_ratio", "dv_ttm"):
        if column in frame:
            result[column] = pd.to_numeric(frame[column], errors="coerce") * 100.0
    for column in ("volume_ratio", "pe", "pe_ttm", "pb", "ps", "ps_ttm"):
        if column in frame:
            result[column] = frame[column]
    result["total_share"] = pd.to_numeric(frame["total_shares"], errors="coerce") / 10000.0
    result["float_share"] = pd.to_numeric(frame["float_shares"], errors="coerce") / 10000.0
    result["free_share"] = pd.to_numeric(frame["free_shares"], errors="coerce") / 10000.0
    result["total_mv"] = pd.to_numeric(frame["total_market_value"], errors="coerce") / 10000.0
    result["circ_mv"] = pd.to_numeric(frame["circulating_market_value"], errors="coerce") / 10000.0
    if "limit_status" in frame:
        result["limit_status"] = frame["limit_status"]
    return result


def _project_adjustment_factor(frame: pd.DataFrame) -> pd.DataFrame:
    if "adjustment_factor" not in frame:
        raise DataSourceContractError("canonical adjustment-factor projection missing adjustment_factor")
    result = _identity(frame)
    result["adj_factor"] = frame["adjustment_factor"]
    return result


def _project_instrument_master(frame: pd.DataFrame) -> pd.DataFrame:
    if "instrument" not in frame:
        raise DataSourceContractError("canonical instrument master missing instrument")
    result = pd.DataFrame({"ts_code": frame["instrument"].astype(str).map(qlib_to_ts)})
    mapping = (
        ("symbol", "symbol"),
        ("name", "name"),
        ("area", "area"),
        ("industry", "industry"),
        ("market", "market"),
        ("exchange", "exchange"),
        ("listing_status", "list_status"),
        ("list_date", "list_date"),
        ("delist_date", "delist_date"),
        ("is_hs", "is_hs"),
        ("act_name", "act_name"),
        ("act_ent_type", "act_ent_type"),
    )
    for source_name, target_name in mapping:
        if source_name in frame:
            result[target_name] = frame[source_name]
    return result


def _project_trading_calendar(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"trading_date", "is_open"}
    missing = required - set(frame.columns)
    if missing:
        raise DataSourceContractError(f"canonical calendar projection missing columns: {sorted(missing)}")
    result = pd.DataFrame()
    result["exchange"] = frame["exchange"] if "exchange" in frame else ""
    result["cal_date"] = pd.to_datetime(frame["trading_date"], errors="raise").dt.strftime("%Y%m%d")
    result["is_open"] = frame["is_open"].astype(int)
    if "previous_trading_date" in frame:
        previous = pd.to_datetime(frame["previous_trading_date"], errors="coerce")
        result["pretrade_date"] = previous.dt.strftime("%Y%m%d")
    else:
        result["pretrade_date"] = None
    return result


def _string_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None
