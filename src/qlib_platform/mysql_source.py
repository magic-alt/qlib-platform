"""Deprecated compatibility surface for the MySQL data-source adapter."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from .data.sources.mysql import (
    MysqlClient,
    build_connection_kwargs,
    build_lean_canonical_endpoints,
    build_lean_canonical_range_endpoints,
    build_mysql_endpoints,
    fetch_lean_benchmark,
    fetch_lean_universe_intervals,
    lean_mysql_preflight,
)

_target = import_module(".data.sources.mysql", __package__)

__all__ = [
    "MysqlClient",
    "build_connection_kwargs",
    "build_lean_canonical_endpoints",
    "build_lean_canonical_range_endpoints",
    "build_mysql_endpoints",
    "fetch_lean_benchmark",
    "fetch_lean_universe_intervals",
    "lean_mysql_preflight",
]


def __getattr__(name: str) -> Any:
    return getattr(_target, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_target)))
