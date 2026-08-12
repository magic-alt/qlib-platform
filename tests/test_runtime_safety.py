from __future__ import annotations

from types import SimpleNamespace

import pytest

from tushare_qlib.runtime_safety import (
    resolve_qlib_parallel_runtime,
    validate_multiprocessing_runtime,
)


def test_windows_process_backend_rejects_stdin_entrypoint():
    with pytest.raises(RuntimeError, match="Unsafe Windows multiprocessing entrypoint"):
        validate_multiprocessing_runtime(4, "loky", platform_name="nt", main_file="<stdin>")


def test_windows_single_kernel_allows_diagnostic_stdin_entrypoint():
    validate_multiprocessing_runtime(1, "multiprocessing", platform_name="nt", main_file="<stdin>")


def test_threading_backend_does_not_require_spawn_importable_main():
    validate_multiprocessing_runtime(4, "threading", platform_name="nt", main_file="<stdin>")


def test_windows_console_launcher_is_a_safe_entrypoint(tmp_path):
    launcher = tmp_path / "pytest.exe"
    launcher.write_bytes(b"launcher")

    validate_multiprocessing_runtime(
        4,
        "loky",
        platform_name="nt",
        main_file=str(launcher / "__main__.py"),
    )


def test_windows_missing_console_launcher_is_rejected(tmp_path):
    entrypoint = tmp_path / "missing.exe" / "__main__.py"

    with pytest.raises(RuntimeError, match="not safely importable"):
        validate_multiprocessing_runtime(4, "loky", platform_name="nt", main_file=str(entrypoint))


def test_runtime_defaults_to_loky(monkeypatch):
    monkeypatch.setattr("tushare_qlib.runtime_safety.os.name", "posix")
    runtime = resolve_qlib_parallel_runtime(
        SimpleNamespace(data={"research": {"qlib_kernels": 6}})
    )

    assert runtime.kernels == 6
    assert runtime.joblib_backend == "loky"


def test_runtime_rejects_unknown_backend():
    with pytest.raises(ValueError, match="joblib_backend"):
        validate_multiprocessing_runtime(4, "unknown", platform_name="posix")


def test_runtime_rejects_non_positive_kernel_count():
    with pytest.raises(ValueError, match="at least 1"):
        validate_multiprocessing_runtime(0, "loky", platform_name="posix")
