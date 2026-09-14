from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

import pandas as pd


FETCH_STATUSES = frozenset(
    {
        "success",
        "empty",
        "incomplete",
        "permission_denied",
        "rate_limited",
        "timeout",
        "unsupported",
        "schema_mismatch",
        "conflict",
        "provider_error",
    }
)


class DataSourceContractError(RuntimeError):
    """Raised when a semantic data-source result cannot satisfy a request."""


@dataclass(frozen=True)
class DatasetRequest:
    """Provider-neutral request for one canonical dataset.

    Provider endpoint names, pagination cursors, and vendor-specific parameters
    deliberately do not belong here. They are adapter implementation details.
    """

    dataset_kind: str
    instruments: tuple[str, ...] = ()
    universe: str | None = None
    start: str | None = None
    end: str | None = None
    frequency: str = "day"
    fields: tuple[str, ...] = ()
    adjustment: str = "raw"
    as_of: str | None = None
    calendar: str | None = None
    require_complete: bool = True
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        if not self.dataset_kind.strip():
            raise ValueError("dataset_kind must not be empty")
        if not self.frequency.strip():
            raise ValueError("frequency must not be empty")
        if not self.adjustment.strip():
            raise ValueError("adjustment must not be empty")
        if not self.schema_version.strip():
            raise ValueError("schema_version must not be empty")
        if self.start and self.end and pd.Timestamp(self.start) > pd.Timestamp(self.end):
            raise ValueError("dataset request start must not be after end")


@dataclass(frozen=True)
class DatasetCapability:
    dataset_kind: str
    schema_versions: tuple[str, ...] = ("1.0",)
    frequencies: tuple[str, ...] = ("day",)
    adjustments: tuple[str, ...] = ("raw",)

    def supports(self, request: DatasetRequest) -> bool:
        return (
            request.dataset_kind == self.dataset_kind
            and request.schema_version in self.schema_versions
            and request.frequency in self.frequencies
            and request.adjustment in self.adjustments
        )


@dataclass(frozen=True)
class SourceCapabilities:
    """Static interface support, intentionally separate from account entitlement."""

    provider: str
    datasets: tuple[DatasetCapability, ...]

    def negotiate(self, request: DatasetRequest) -> DatasetCapability:
        for capability in self.datasets:
            if capability.supports(request):
                return capability
        kinds = sorted({capability.dataset_kind for capability in self.datasets})
        raise DataSourceContractError(
            f"provider {self.provider!r} does not support request "
            f"kind={request.dataset_kind!r}, schema={request.schema_version!r}, "
            f"frequency={request.frequency!r}, adjustment={request.adjustment!r}; "
            f"available kinds={kinds}"
        )


@dataclass(frozen=True)
class Coverage:
    start: str | None
    end: str | None
    row_count: int
    complete: bool
    watermark: str | None = None


@dataclass(frozen=True)
class CanonicalBatch:
    """Canonical values plus the semantic metadata needed to interpret them."""

    dataset_kind: str
    data: pd.DataFrame
    schema_version: str
    timezone: str
    source_units: Mapping[str, str] = field(default_factory=dict)
    canonical_units: Mapping[str, str] = field(default_factory=dict)
    event_time_column: str = "event_time"
    available_at_column: str | None = "available_at"
    coverage: Coverage | None = None


@dataclass(frozen=True)
class PaginationPolicy:
    """Bounded provider paging policy; provider cursor syntax stays adapter-local."""

    page_size: int = 1000
    max_pages: int = 100

    def __post_init__(self) -> None:
        if self.page_size < 1:
            raise ValueError("pagination page_size must be positive")
        if self.max_pages < 1:
            raise ValueError("pagination max_pages must be positive")


@dataclass(frozen=True)
class PaginationCursor:
    """Auditable resume point bound to one exact semantic request."""

    provider: str
    dataset_kind: str
    request_fingerprint: str
    next_offset: int

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("pagination cursor provider must not be empty")
        if not self.dataset_kind.strip():
            raise ValueError("pagination cursor dataset_kind must not be empty")
        if not self.request_fingerprint.strip():
            raise ValueError("pagination cursor request_fingerprint must not be empty")
        if self.next_offset < 0:
            raise ValueError("pagination cursor next_offset must not be negative")


@dataclass(frozen=True)
class PaginationEvidence:
    request_fingerprint: str
    start_offset: int
    page_size: int
    page_count: int
    row_count: int
    terminal: bool
    next_cursor: PaginationCursor | None = None


@dataclass(frozen=True)
class FetchEnvelope:
    """Auditable provider result without collapsing distinct failure classes."""

    provider: str
    status: str
    attempts: int
    batch: CanonicalBatch | None = None
    provider_revision: str | None = None
    source_hash: str | None = None
    entitlement: str = "unknown"
    error_class: str | None = None
    error: str | None = None
    pagination: PaginationEvidence | None = None
    ingested_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        if self.status not in FETCH_STATUSES:
            raise ValueError(f"unsupported fetch status: {self.status}")
        if self.attempts < 1:
            raise ValueError("attempts must be positive")

    @property
    def succeeded(self) -> bool:
        return self.status == "success" and self.batch is not None


@runtime_checkable
class SemanticDataSource(Protocol):
    @property
    def capabilities(self) -> SourceCapabilities: ...

    def fetch_dataset(self, request: DatasetRequest) -> FetchEnvelope: ...


def request_fingerprint(request: DatasetRequest) -> str:
    payload = {
        "adjustment": request.adjustment,
        "as_of": request.as_of,
        "calendar": request.calendar,
        "dataset_kind": request.dataset_kind,
        "end": request.end,
        "fields": list(request.fields),
        "frequency": request.frequency,
        "instruments": list(request.instruments),
        "require_complete": request.require_complete,
        "schema_version": request.schema_version,
        "start": request.start,
        "universe": request.universe,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def frame_sha256(frame: pd.DataFrame) -> str:
    """Content fingerprint for in-memory provider payload provenance."""

    digest = hashlib.sha256()
    digest.update("\x1f".join(map(str, frame.columns)).encode("utf-8"))
    digest.update(pd.util.hash_pandas_object(frame, index=True).values.tobytes())
    return digest.hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_coverage(
    frame: pd.DataFrame,
    *,
    event_time_column: str,
    request: DatasetRequest,
) -> Coverage:
    if frame.empty or event_time_column not in frame.columns:
        return Coverage(None, None, len(frame), False, None)
    timestamps = pd.to_datetime(frame[event_time_column], errors="raise", utc=True)
    start = timestamps.min()
    end = timestamps.max()
    complete = True
    if request.start is not None:
        complete = complete and start.date() <= pd.Timestamp(request.start).date()
    if request.end is not None:
        complete = complete and end.date() >= pd.Timestamp(request.end).date()
    return Coverage(start.isoformat(), end.isoformat(), len(frame), complete, end.isoformat())


def validate_canonical_batch(
    batch: CanonicalBatch,
    request: DatasetRequest,
    *,
    key_columns: tuple[str, ...] = ("instrument", "event_time"),
) -> str | None:
    """Return an auditable failure status instead of repairing bad provider data."""

    if batch.dataset_kind != request.dataset_kind or batch.schema_version != request.schema_version:
        return "schema_mismatch"
    if batch.event_time_column not in batch.data.columns:
        return "schema_mismatch"
    if request.fields:
        missing = set(request.fields) - set(batch.data.columns)
        if missing:
            return "schema_mismatch"
    usable_keys = [column for column in key_columns if column in batch.data.columns]
    if usable_keys and batch.data.duplicated(usable_keys).any():
        return "conflict"
    coverage = batch.coverage or canonical_coverage(
        batch.data, event_time_column=batch.event_time_column, request=request
    )
    if request.require_complete and not coverage.complete:
        return "incomplete"
    return None


def collect_paginated(
    *,
    provider: str,
    request: DatasetRequest,
    page_fetcher: Callable[[int, int], FetchEnvelope],
    policy: PaginationPolicy | None = None,
    cursor: PaginationCursor | None = None,
) -> FetchEnvelope:
    """Collect bounded canonical pages and return explicit resume evidence on interruption.

    A cursor resumes provider fetching at ``next_offset`` only. Persisting already
    accepted pages belongs to the caller's storage boundary, so a resumed tail is
    always marked incomplete until that caller combines it with its durable prefix.
    """

    resolved_policy = policy or PaginationPolicy()
    fingerprint = request_fingerprint(request)
    start_offset = 0 if cursor is None else _validate_cursor(cursor, provider, request, fingerprint)
    offset = start_offset
    pages: list[CanonicalBatch] = []
    hashes: list[str] = []
    attempts = 0
    page_count = 0
    terminal = False
    provider_revision: str | None = None
    entitlement = "unknown"

    for _ in range(resolved_policy.max_pages):
        envelope = page_fetcher(offset, resolved_policy.page_size)
        attempts += envelope.attempts
        if envelope.provider != provider:
            return _pagination_failure(
                provider=provider,
                request=request,
                status="conflict",
                attempts=attempts,
                pages=pages,
                hashes=hashes,
                policy=resolved_policy,
                fingerprint=fingerprint,
                start_offset=start_offset,
                next_offset=offset,
                page_count=page_count,
                provider_revision=provider_revision,
                entitlement=entitlement,
                error_class="provider_mismatch",
                error=f"pagination page provider changed from {provider!r} to {envelope.provider!r}",
            )
        if envelope.status == "empty":
            terminal = True
            break
        if not envelope.succeeded or envelope.batch is None:
            return _pagination_failure(
                provider=provider,
                request=request,
                status=envelope.status,
                attempts=attempts,
                pages=pages,
                hashes=hashes,
                policy=resolved_policy,
                fingerprint=fingerprint,
                start_offset=start_offset,
                next_offset=offset,
                page_count=page_count,
                provider_revision=provider_revision or envelope.provider_revision,
                entitlement=envelope.entitlement,
                error_class=envelope.error_class,
                error=envelope.error,
            )
        consistency_error = _page_contract_error(pages[0] if pages else None, envelope.batch)
        if consistency_error is not None:
            return _pagination_failure(
                provider=provider,
                request=request,
                status="schema_mismatch",
                attempts=attempts,
                pages=pages,
                hashes=hashes,
                policy=resolved_policy,
                fingerprint=fingerprint,
                start_offset=start_offset,
                next_offset=offset,
                page_count=page_count,
                provider_revision=provider_revision or envelope.provider_revision,
                entitlement=envelope.entitlement,
                error_class="page_contract_drift",
                error=consistency_error,
            )
        if provider_revision is not None and envelope.provider_revision not in {None, provider_revision}:
            return _pagination_failure(
                provider=provider,
                request=request,
                status="conflict",
                attempts=attempts,
                pages=pages,
                hashes=hashes,
                policy=resolved_policy,
                fingerprint=fingerprint,
                start_offset=start_offset,
                next_offset=offset,
                page_count=page_count,
                provider_revision=provider_revision,
                entitlement=envelope.entitlement,
                error_class="provider_revision_drift",
                error="provider revision changed during one paginated fetch",
            )
        provider_revision = provider_revision or envelope.provider_revision
        entitlement = envelope.entitlement
        pages.append(envelope.batch)
        if envelope.source_hash:
            hashes.append(envelope.source_hash)
        page_count += 1
        row_count = len(envelope.batch.data)
        offset += row_count
        if row_count < resolved_policy.page_size:
            terminal = True
            break

    if not pages:
        status = "incomplete" if request.require_complete or start_offset > 0 else "empty"
        error_class = None
        error = None
        if start_offset > 0:
            error_class = "resume_prefix_required"
            error = "resumed pagination segment requires the caller's durable prefix"
        elif status == "incomplete":
            error_class = "required_data_empty"
            error = "required paginated dataset returned no rows"
        return FetchEnvelope(
            provider,
            status,
            max(attempts, 1),
            provider_revision=provider_revision,
            entitlement=entitlement,
            error_class=error_class,
            error=error,
            pagination=PaginationEvidence(
                fingerprint,
                start_offset,
                resolved_policy.page_size,
                0,
                0,
                terminal,
                None,
            ),
        )

    batch = _merge_pages(pages, request)
    failure = validate_canonical_batch(batch, request)
    result_error: str | None = None
    if start_offset > 0 and failure is None:
        failure = "resume_prefix_required"
        result_error = "resumed pagination segment requires the caller's durable prefix before validation"
    elif not terminal and failure is None:
        failure = "incomplete"
        result_error = "pagination limit reached before a terminal page"
    elif terminal and failure == "incomplete":
        result_error = "terminal page sequence does not satisfy required dataset coverage"
    status = "incomplete" if failure == "resume_prefix_required" else failure or "success"
    next_cursor = None
    if not terminal:
        next_cursor = PaginationCursor(provider, request.dataset_kind, fingerprint, offset)
    evidence = PaginationEvidence(
        fingerprint,
        start_offset,
        resolved_policy.page_size,
        page_count,
        len(batch.data),
        terminal,
        next_cursor,
    )
    return FetchEnvelope(
        provider,
        status,
        max(attempts, 1),
        batch=batch,
        provider_revision=provider_revision,
        source_hash=_combined_hash(hashes),
        entitlement=entitlement,
        error_class=failure,
        error=result_error,
        pagination=evidence,
    )


def _validate_cursor(
    cursor: PaginationCursor,
    provider: str,
    request: DatasetRequest,
    fingerprint: str,
) -> int:
    if cursor.provider != provider:
        raise DataSourceContractError("pagination cursor provider does not match the requested provider")
    if cursor.dataset_kind != request.dataset_kind:
        raise DataSourceContractError("pagination cursor dataset kind does not match the request")
    if cursor.request_fingerprint != fingerprint:
        raise DataSourceContractError("pagination cursor request fingerprint does not match the request")
    return cursor.next_offset


def _page_contract_error(first: CanonicalBatch | None, current: CanonicalBatch) -> str | None:
    if first is None:
        return None
    checks = (
        (first.dataset_kind, current.dataset_kind, "dataset_kind"),
        (first.schema_version, current.schema_version, "schema_version"),
        (first.timezone, current.timezone, "timezone"),
        (first.event_time_column, current.event_time_column, "event_time_column"),
        (dict(first.source_units), dict(current.source_units), "source_units"),
        (dict(first.canonical_units), dict(current.canonical_units), "canonical_units"),
    )
    for expected, actual, field_name in checks:
        if expected != actual:
            return f"paginated page {field_name} changed during collection"
    return None


def _merge_pages(pages: list[CanonicalBatch], request: DatasetRequest) -> CanonicalBatch:
    first = pages[0]
    frame = pd.concat([page.data for page in pages], ignore_index=True)
    coverage = canonical_coverage(frame, event_time_column=first.event_time_column, request=request)
    return CanonicalBatch(
        dataset_kind=first.dataset_kind,
        data=frame,
        schema_version=first.schema_version,
        timezone=first.timezone,
        source_units=first.source_units,
        canonical_units=first.canonical_units,
        event_time_column=first.event_time_column,
        available_at_column=first.available_at_column,
        coverage=coverage,
    )


def _partial_batch(pages: list[CanonicalBatch], request: DatasetRequest) -> CanonicalBatch | None:
    return _merge_pages(pages, request) if pages else None


def _combined_hash(hashes: list[str]) -> str | None:
    if not hashes:
        return None
    digest = hashlib.sha256()
    for value in hashes:
        digest.update(value.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _pagination_failure(
    *,
    provider: str,
    request: DatasetRequest,
    status: str,
    attempts: int,
    pages: list[CanonicalBatch],
    hashes: list[str],
    policy: PaginationPolicy,
    fingerprint: str,
    start_offset: int,
    next_offset: int,
    page_count: int,
    provider_revision: str | None,
    entitlement: str,
    error_class: str | None,
    error: str | None,
) -> FetchEnvelope:
    partial = _partial_batch(pages, request)
    return FetchEnvelope(
        provider,
        status,
        max(attempts, 1),
        batch=partial,
        provider_revision=provider_revision,
        source_hash=_combined_hash(hashes),
        entitlement=entitlement,
        error_class=error_class,
        error=error,
        pagination=PaginationEvidence(
            fingerprint,
            start_offset,
            policy.page_size,
            page_count,
            0 if partial is None else len(partial.data),
            False,
            PaginationCursor(provider, request.dataset_kind, fingerprint, next_offset),
        ),
    )


def require_usable(envelope: FetchEnvelope, request: DatasetRequest) -> CanonicalBatch:
    if not envelope.succeeded or envelope.batch is None:
        detail = f": {envelope.error}" if envelope.error else ""
        raise DataSourceContractError(
            f"provider {envelope.provider!r} returned {envelope.status!r} "
            f"for required dataset {request.dataset_kind!r}{detail}"
        )
    failure = validate_canonical_batch(envelope.batch, request)
    if failure is not None:
        raise DataSourceContractError(
            f"provider {envelope.provider!r} returned {failure!r} canonical data for {request.dataset_kind!r}"
        )
    return envelope.batch


@dataclass(frozen=True)
class LocalDatasetSpec:
    dataset_kind: str
    path: Path
    schema_version: str = "1.0"
    frequency: str = "day"
    adjustment: str = "raw"
    timezone: str = "UTC"
    source_units: Mapping[str, str] = field(default_factory=dict)
    canonical_units: Mapping[str, str] = field(default_factory=dict)
    event_time_column: str = "event_time"
    expected_sha256: str | None = None


class LocalFileDataSource:
    """Read-only adapter for explicit immutable canonical CSV/Parquet files."""

    def __init__(self, specs: Mapping[str, LocalDatasetSpec], *, provider: str = "local_file") -> None:
        self._specs = dict(specs)
        self._provider = provider

    @property
    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            provider=self._provider,
            datasets=tuple(
                DatasetCapability(
                    spec.dataset_kind,
                    (spec.schema_version,),
                    (spec.frequency,),
                    (spec.adjustment,),
                )
                for spec in self._specs.values()
            ),
        )

    def fetch_dataset(self, request: DatasetRequest) -> FetchEnvelope:
        try:
            self.capabilities.negotiate(request)
        except DataSourceContractError as exc:
            return FetchEnvelope(self._provider, "unsupported", 1, error_class="unsupported", error=str(exc))
        spec = self._specs[request.dataset_kind]
        path = spec.path
        if not path.is_file():
            return FetchEnvelope(
                self._provider,
                "provider_error",
                1,
                error_class="missing_file",
                error=f"canonical source file does not exist: {path}",
            )
        digest = file_sha256(path)
        if spec.expected_sha256 is not None and digest != spec.expected_sha256:
            return FetchEnvelope(
                self._provider,
                "conflict",
                1,
                source_hash=digest,
                error_class="checksum_mismatch",
                error="immutable canonical source checksum does not match the expected digest",
            )
        if path.suffix.lower() == ".parquet":
            frame = pd.read_parquet(path)
        elif path.suffix.lower() == ".csv":
            frame = pd.read_csv(path)
        else:
            return FetchEnvelope(
                self._provider,
                "unsupported",
                1,
                source_hash=digest,
                error_class="unsupported_format",
                error=f"unsupported canonical file format: {path.suffix}",
            )
        frame = _filter_canonical_frame(frame, request, spec.event_time_column)
        coverage = canonical_coverage(frame, event_time_column=spec.event_time_column, request=request)
        batch = CanonicalBatch(
            dataset_kind=spec.dataset_kind,
            data=frame,
            schema_version=spec.schema_version,
            timezone=spec.timezone,
            source_units=spec.source_units,
            canonical_units=spec.canonical_units,
            event_time_column=spec.event_time_column,
            coverage=coverage,
        )
        failure = validate_canonical_batch(batch, request)
        status = failure or ("empty" if frame.empty else "success")
        return FetchEnvelope(
            self._provider,
            status,
            1,
            batch=batch,
            source_hash=digest,
            entitlement="granted",
            error_class=failure,
        )


def _filter_canonical_frame(
    frame: pd.DataFrame,
    request: DatasetRequest,
    event_time_column: str,
) -> pd.DataFrame:
    result = frame.copy()
    if event_time_column in result.columns and (request.start or request.end):
        timestamps = pd.to_datetime(result[event_time_column], errors="raise", utc=True)
        if request.start:
            result = result.loc[timestamps.dt.date >= pd.Timestamp(request.start).date()].copy()
            timestamps = pd.to_datetime(result[event_time_column], errors="raise", utc=True)
        if request.end:
            result = result.loc[timestamps.dt.date <= pd.Timestamp(request.end).date()].copy()
    if request.instruments and "instrument" in result.columns:
        result = result.loc[result["instrument"].astype(str).isin(request.instruments)].copy()
    if request.fields:
        identity = [
            column
            for column in ("instrument", "trading_date", event_time_column, "available_at")
            if column in result.columns
        ]
        selected = list(dict.fromkeys([*identity, *request.fields]))
        missing = set(selected) - set(result.columns)
        if not missing:
            result = result[selected].copy()
    return result.reset_index(drop=True)


class RecordedDataSource:
    """Deterministic offline adapter for golden fixtures and failure replay."""

    def __init__(
        self,
        records: Mapping[str, FetchEnvelope],
        capabilities: SourceCapabilities,
    ) -> None:
        self._records = dict(records)
        self._capabilities = capabilities

    @property
    def capabilities(self) -> SourceCapabilities:
        return self._capabilities

    def fetch_dataset(self, request: DatasetRequest) -> FetchEnvelope:
        try:
            self.capabilities.negotiate(request)
        except DataSourceContractError as exc:
            return FetchEnvelope(
                self.capabilities.provider,
                "unsupported",
                1,
                error_class="unsupported",
                error=str(exc),
            )
        envelope = self._records.get(request.dataset_kind)
        if envelope is None:
            return FetchEnvelope(
                self.capabilities.provider,
                "unsupported",
                1,
                error_class="unrecorded_request",
                error=f"no recorded result for {request.dataset_kind}",
            )
        return envelope
