from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar


T = TypeVar("T")
R = TypeVar("R")


@dataclass(frozen=True)
class ResourceHints:
    cpus: float = 1.0
    gpus: float = 0.0
    memory_gb: float | None = None

    def validate(self) -> None:
        if self.cpus <= 0 or self.gpus < 0:
            raise ValueError("resource hints require cpus > 0 and gpus >= 0")
        if self.memory_gb is not None and self.memory_gb <= 0:
            raise ValueError("memory_gb must be positive when provided")


@dataclass(frozen=True)
class ParallelizationPolicy:
    min_jobs: int = 16
    min_estimated_cpu_seconds: float = 600.0

    def should_parallelize(self, *, job_count: int, estimated_seconds_per_job: float) -> bool:
        if job_count < 0 or estimated_seconds_per_job < 0:
            raise ValueError("job count and duration estimate must be non-negative")
        total = job_count * estimated_seconds_per_job
        return job_count >= self.min_jobs and total >= self.min_estimated_cpu_seconds


@dataclass(frozen=True)
class BenchmarkResult:
    backend: str
    jobs: int
    elapsed_seconds: float
    jobs_per_second: float


class ResearchExecutor(Protocol, Generic[T, R]):
    backend: str

    def map(self, function: Callable[[T], R], items: Sequence[T]) -> list[R]: ...
    def close(self) -> None: ...
