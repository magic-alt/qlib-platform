from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Protocol

from qlib_platform.control_plane.secrets import SecretRef


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _required(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be non-empty")
    return normalized


@dataclass(frozen=True)
class ExecutionBudget:
    cpu_cores: float = 1.0
    memory_gb: float = 1.0
    runtime_seconds: int = 3600

    def __post_init__(self) -> None:
        if self.cpu_cores <= 0 or self.memory_gb <= 0 or self.runtime_seconds <= 0:
            raise ValueError("execution budget values must be positive")

    @property
    def cpu_hours(self) -> float:
        return self.cpu_cores * self.runtime_seconds / 3600.0


@dataclass(frozen=True)
class ProjectQuota:
    max_concurrency: int = 2
    max_matrix_jobs: int = 100
    max_cpu_hours_per_run: float = 24.0
    max_memory_gb: float = 64.0
    max_runtime_seconds: int = 86400

    def validate(self, request: ExecutionRequest, *, active_runs: int) -> None:
        if self.max_concurrency < 1 or self.max_matrix_jobs < 1:
            raise ValueError("project quota concurrency and matrix limits must be positive")
        if active_runs >= self.max_concurrency:
            raise RuntimeError("project concurrency quota exceeded")
        if request.matrix_jobs > self.max_matrix_jobs:
            raise RuntimeError("experiment matrix job budget exceeded")
        if request.budget.cpu_hours > self.max_cpu_hours_per_run:
            raise RuntimeError("CPU-hour budget exceeded")
        if request.budget.memory_gb > self.max_memory_gb:
            raise RuntimeError("memory budget exceeded")
        if request.budget.runtime_seconds > self.max_runtime_seconds:
            raise RuntimeError("runtime budget exceeded")


@dataclass(frozen=True)
class ExecutionRequest:
    """Versioned control-plane job envelope; no arbitrary shell/python payload is accepted."""

    project_id: str
    operation: str
    descriptor_ref: str
    config_ref: str
    artifact_refs: tuple[str, ...] = ()
    secret_refs: tuple[SecretRef, ...] = ()
    budget: ExecutionBudget = ExecutionBudget()
    matrix_jobs: int = 1

    def __post_init__(self) -> None:
        for field_name in ("project_id", "operation", "descriptor_ref", "config_ref"):
            _required(str(getattr(self, field_name)), field_name)
        if self.matrix_jobs < 1:
            raise ValueError("matrix_jobs must be positive")
        if any(ref.project_id != self.project_id for ref in self.secret_refs):
            raise ValueError("all secret refs must belong to the execution project")

    def business_payload(self) -> dict[str, Any]:
        return {
            "schema": "qlib-platform.execution-request.v1",
            "project_id": self.project_id,
            "operation": self.operation,
            "descriptor_ref": self.descriptor_ref,
            "config_ref": self.config_ref,
            "artifact_refs": list(self.artifact_refs),
            "secret_refs": [ref.as_dict() for ref in self.secret_refs],
            "budget": {
                "cpu_cores": self.budget.cpu_cores,
                "memory_gb": self.budget.memory_gb,
                "runtime_seconds": self.budget.runtime_seconds,
            },
            "matrix_jobs": self.matrix_jobs,
        }

    @property
    def business_id(self) -> str:
        digest = hashlib.sha256(_canonical_json(self.business_payload()).encode("utf-8")).hexdigest()
        return f"run-{digest}"


@dataclass(frozen=True)
class ExecutionOutcome:
    artifact_ids: tuple[str, ...]
    manifest_digest: str

    def __post_init__(self) -> None:
        if len(self.manifest_digest) != 64 or any(c not in "0123456789abcdef" for c in self.manifest_digest):
            raise ValueError("manifest_digest must be lowercase SHA-256 hex")


class ExecutionStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class ExecutionRecord:
    project_id: str
    business_id: str
    status: ExecutionStatus
    attempts: int = 0
    artifact_ids: tuple[str, ...] = ()
    manifest_digest: str | None = None
    last_delivery_id: str | None = None
    error: str | None = None


class ExecutionStore(Protocol):
    def get(self, project_id: str, business_id: str) -> ExecutionRecord | None: ...

    def put(self, record: ExecutionRecord) -> ExecutionRecord: ...

    def active_count(self, project_id: str) -> int: ...


class MemoryExecutionStore:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], ExecutionRecord] = {}

    def get(self, project_id: str, business_id: str) -> ExecutionRecord | None:
        return self._records.get((project_id, business_id))

    def put(self, record: ExecutionRecord) -> ExecutionRecord:
        self._records[(record.project_id, record.business_id)] = record
        return record

    def active_count(self, project_id: str) -> int:
        active = {ExecutionStatus.QUEUED, ExecutionStatus.RUNNING}
        return sum(1 for record in self._records.values() if record.project_id == project_id and record.status in active)


ExecutionHandler = Callable[[ExecutionRequest], ExecutionOutcome]


class LocalExecutionBackend:
    """Golden reference backend. It executes the immutable request synchronously."""

    backend = "local"

    def __init__(
        self,
        handler: ExecutionHandler,
        *,
        store: ExecutionStore | None = None,
        quota: ProjectQuota | None = None,
    ) -> None:
        self.handler = handler
        self.store = store or MemoryExecutionStore()
        self.quota = quota or ProjectQuota()

    def submit(self, request: ExecutionRequest) -> ExecutionRecord:
        existing = self.store.get(request.project_id, request.business_id)
        if existing is not None:
            return existing
        self.quota.validate(request, active_runs=self.store.active_count(request.project_id))
        self.store.put(ExecutionRecord(request.project_id, request.business_id, ExecutionStatus.QUEUED))
        return self._run(request)

    def cancel(self, request: ExecutionRequest) -> ExecutionRecord:
        existing = self.store.get(request.project_id, request.business_id)
        if existing is None:
            return self.store.put(
                ExecutionRecord(request.project_id, request.business_id, ExecutionStatus.CANCELLED)
            )
        if existing.status in {ExecutionStatus.SUCCEEDED, ExecutionStatus.CANCELLED}:
            return existing
        return self.store.put(replace(existing, status=ExecutionStatus.CANCELLED))

    def resume(self, request: ExecutionRequest) -> ExecutionRecord:
        existing = self.store.get(request.project_id, request.business_id)
        if existing is None:
            return self.submit(request)
        if existing.status not in {ExecutionStatus.CANCELLED, ExecutionStatus.FAILED}:
            return existing
        self.quota.validate(request, active_runs=self.store.active_count(request.project_id))
        self.store.put(replace(existing, status=ExecutionStatus.QUEUED, error=None))
        return self._run(request)

    def _run(self, request: ExecutionRequest) -> ExecutionRecord:
        current = self.store.get(request.project_id, request.business_id)
        attempts = (current.attempts if current else 0) + 1
        running = ExecutionRecord(
            request.project_id,
            request.business_id,
            ExecutionStatus.RUNNING,
            attempts=attempts,
        )
        self.store.put(running)
        try:
            outcome = self.handler(request)
        except Exception as exc:
            failed = replace(running, status=ExecutionStatus.FAILED, error=str(exc))
            self.store.put(failed)
            raise
        succeeded = replace(
            running,
            status=ExecutionStatus.SUCCEEDED,
            artifact_ids=outcome.artifact_ids,
            manifest_digest=outcome.manifest_digest,
        )
        return self.store.put(succeeded)


class FakeWorkerExecutionBackend:
    """Queue/worker semantics fixture used to certify idempotency and delivery behavior."""

    backend = "fake-worker"

    def __init__(
        self,
        handler: ExecutionHandler,
        *,
        store: ExecutionStore | None = None,
        quota: ProjectQuota | None = None,
    ) -> None:
        self.handler = handler
        self.store = store or MemoryExecutionStore()
        self.quota = quota or ProjectQuota()

    def submit(self, request: ExecutionRequest) -> ExecutionRecord:
        existing = self.store.get(request.project_id, request.business_id)
        if existing is not None:
            return existing
        self.quota.validate(request, active_runs=self.store.active_count(request.project_id))
        return self.store.put(
            ExecutionRecord(request.project_id, request.business_id, ExecutionStatus.QUEUED)
        )

    def deliver(self, request: ExecutionRequest, *, delivery_id: str) -> ExecutionRecord:
        delivery = _required(delivery_id, "delivery_id")
        existing = self.store.get(request.project_id, request.business_id)
        if existing is None:
            existing = self.submit(request)
        if existing.status in {ExecutionStatus.SUCCEEDED, ExecutionStatus.CANCELLED}:
            return existing
        attempts = existing.attempts + 1
        running = replace(
            existing,
            status=ExecutionStatus.RUNNING,
            attempts=attempts,
            last_delivery_id=delivery,
            error=None,
        )
        self.store.put(running)
        try:
            outcome = self.handler(request)
        except Exception as exc:
            failed = replace(running, status=ExecutionStatus.FAILED, error=str(exc))
            self.store.put(failed)
            raise
        succeeded = replace(
            running,
            status=ExecutionStatus.SUCCEEDED,
            artifact_ids=outcome.artifact_ids,
            manifest_digest=outcome.manifest_digest,
        )
        return self.store.put(succeeded)

    def cancel(self, request: ExecutionRequest) -> ExecutionRecord:
        existing = self.store.get(request.project_id, request.business_id)
        if existing is None:
            existing = self.submit(request)
        if existing.status in {ExecutionStatus.SUCCEEDED, ExecutionStatus.CANCELLED}:
            return existing
        return self.store.put(replace(existing, status=ExecutionStatus.CANCELLED))

    def resume(self, request: ExecutionRequest) -> ExecutionRecord:
        existing = self.store.get(request.project_id, request.business_id)
        if existing is None:
            return self.submit(request)
        if existing.status not in {ExecutionStatus.CANCELLED, ExecutionStatus.FAILED}:
            return existing
        self.quota.validate(request, active_runs=self.store.active_count(request.project_id))
        return self.store.put(replace(existing, status=ExecutionStatus.QUEUED, error=None))
