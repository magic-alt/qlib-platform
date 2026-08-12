from __future__ import annotations

import __main__
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


_SUPPORTED_JOBLIB_BACKENDS = {"loky", "multiprocessing", "threading"}
_UNSAFE_MAIN_FILES = {"<stdin>", "<string>", "-", ""}


@dataclass(frozen=True)
class QlibParallelRuntime:
    kernels: int
    joblib_backend: str

    def qlib_init_kwargs(self) -> dict[str, object]:
        return {"kernels": self.kernels, "joblib_backend": self.joblib_backend}


def _research_mapping(settings: Any) -> Mapping[str, Any]:
    research = settings.data.get("research", {})
    return research if isinstance(research, Mapping) else {}


def _main_file() -> str | None:
    value = getattr(__main__, "__file__", None)
    return None if value is None else str(value)


def _is_importable_entrypoint(entrypoint: str) -> bool:
    path = Path(entrypoint)
    if path.is_file():
        return True
    return (
        path.name.lower() == "__main__.py"
        and path.parent.suffix.lower() == ".exe"
        and path.parent.is_file()
    )


def validate_multiprocessing_runtime(
    kernels: int,
    backend: str,
    *,
    platform_name: str | None = None,
    main_file: str | None = None,
) -> None:
    """Fail before Qlib can spawn workers from an unsafe Windows entrypoint."""

    if kernels < 1:
        raise ValueError("research.qlib_kernels must be at least 1")
    normalized = str(backend).strip().lower()
    if normalized not in _SUPPORTED_JOBLIB_BACKENDS:
        raise ValueError(
            "research.joblib_backend must be one of "
            f"{sorted(_SUPPORTED_JOBLIB_BACKENDS)}, got {backend!r}"
        )
    if (platform_name or os.name) != "nt" or kernels <= 1 or normalized == "threading":
        return

    entrypoint = _main_file() if main_file is None else str(main_file)
    if entrypoint is None or entrypoint.strip().lower() in _UNSAFE_MAIN_FILES:
        raise RuntimeError(
            "Unsafe Windows multiprocessing entrypoint for Qlib. "
            "Run with `python -m tushare_qlib ...` or a .py file protected by "
            "`if __name__ == '__main__':`; do not pipe code to `python -` or use `python -c`. "
            "Use research.qlib_kernels=1 only for an isolation smoke test."
        )
    if entrypoint.startswith("<") or not _is_importable_entrypoint(entrypoint):
        raise RuntimeError(
            f"Windows Qlib entrypoint is not safely importable: {entrypoint!r}. "
            "Use `python -m tushare_qlib ...` or a guarded .py file."
        )


def resolve_qlib_parallel_runtime(settings: Any) -> QlibParallelRuntime:
    research = _research_mapping(settings)
    kernels = int(research.get("qlib_kernels", 4))
    backend = str(research.get("joblib_backend", "loky")).strip().lower()
    validate_multiprocessing_runtime(kernels, backend)
    return QlibParallelRuntime(kernels=kernels, joblib_backend=backend)
