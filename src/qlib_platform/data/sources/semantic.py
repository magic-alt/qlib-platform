from __future__ import annotations

import hashlib
from collections.abc import Mapping
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
    deliberately do not belong here.  They are adapter implementation details.
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
            f"provider {envelope.provider!r} returned {failure!r} canonical data "
            f"for {request.dataset_kind!r}"
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
