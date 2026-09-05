from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from typing import Generic, TypeVar

from qlib_platform.research.workflow.parallel_types import ResourceHints


T = TypeVar("T")
R = TypeVar("R")


class SerialResearchExecutor(Generic[T, R]):
    backend = "serial"

    def map(self, function: Callable[[T], R], items: Sequence[T]) -> list[R]:
        return [function(item) for item in items]

    def close(self) -> None:
        return


class ProcessResearchExecutor(Generic[T, R]):
    backend = "process"

    def __init__(self, *, max_workers: int | None = None) -> None:
        if max_workers is not None and max_workers < 1:
            raise ValueError("max_workers must be positive")
        self._pool = ProcessPoolExecutor(max_workers=max_workers)

    def map(self, function: Callable[[T], R], items: Sequence[T]) -> list[R]:
        return list(self._pool.map(function, items))

    def close(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=False)


class RayResearchExecutor(Generic[T, R]):
    backend = "ray"

    def __init__(self, *, address: str | None = None, resources: ResourceHints | None = None) -> None:
        try:
            import ray
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Ray backend requires qlib-platform[parallel-ray]") from exc
        self._ray = ray
        self._owns_runtime = not ray.is_initialized()
        if self._owns_runtime:
            ray.init(address=address, ignore_reinit_error=True)
        self.resources = resources or ResourceHints()
        self.resources.validate()

    def map(self, function: Callable[[T], R], items: Sequence[T]) -> list[R]:
        remote = self._ray.remote(
            num_cpus=self.resources.cpus,
            num_gpus=self.resources.gpus,
        )(function)
        return list(self._ray.get([remote.remote(item) for item in items]))

    def close(self) -> None:
        if self._owns_runtime:
            self._ray.shutdown()


class DaskResearchExecutor(Generic[T, R]):
    backend = "dask"

    def __init__(self, *, address: str | None = None) -> None:
        try:
            from distributed import Client
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Dask backend requires qlib-platform[parallel-dask]") from exc
        self._client = Client(address) if address else Client(processes=True)

    def map(self, function: Callable[[T], R], items: Sequence[T]) -> list[R]:
        return list(self._client.gather(self._client.map(function, items)))

    def close(self) -> None:
        self._client.close()
