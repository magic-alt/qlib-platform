from __future__ import annotations

from collections.abc import Callable, Iterable
from time import perf_counter
from typing import Any, TypeVar

from qlib_platform.research.workflow.parallel_backends import (
    DaskResearchExecutor,
    ProcessResearchExecutor,
    RayResearchExecutor,
    SerialResearchExecutor,
)
from qlib_platform.research.workflow.parallel_types import (
    BenchmarkResult,
    ParallelizationPolicy,
    ResearchExecutor,
    ResourceHints,
)


T = TypeVar("T")
R = TypeVar("R")

__all__ = [
    "BenchmarkResult",
    "ParallelizationPolicy",
    "ResourceHints",
    "benchmark_executor",
    "create_research_executor",
]


def create_research_executor(
    backend: str = "serial",
    *,
    max_workers: int | None = None,
    address: str | None = None,
    resources: ResourceHints | None = None,
) -> ResearchExecutor[Any, Any]:
    normalized = backend.strip().lower()
    if normalized == "serial":
        return SerialResearchExecutor()
    if normalized == "process":
        return ProcessResearchExecutor(max_workers=max_workers)
    if normalized == "ray":
        return RayResearchExecutor(address=address, resources=resources)
    if normalized == "dask":
        return DaskResearchExecutor(address=address)
    raise ValueError(f"unknown research execution backend: {backend!r}")


def benchmark_executor(
    executor: ResearchExecutor[T, R],
    function: Callable[[T], R],
    items: Iterable[T],
) -> BenchmarkResult:
    materialized = list(items)
    started = perf_counter()
    executor.map(function, materialized)
    elapsed = perf_counter() - started
    return BenchmarkResult(
        backend=executor.backend,
        jobs=len(materialized),
        elapsed_seconds=elapsed,
        jobs_per_second=(len(materialized) / elapsed if elapsed > 0 else float("inf")),
    )
