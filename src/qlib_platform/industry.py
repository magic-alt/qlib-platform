"""Deprecated compatibility surface for PIT industry classification.

New code should import :mod:`qlib_platform.data.industry`.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_target = import_module(".data.industry", __package__)


def __getattr__(name: str) -> Any:
    return getattr(_target, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_target)))
