"""Compatibility import for the provider-neutral ingestion orchestrator."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from .data.ingestion import Extractor

_legacy = import_module("._extract_legacy", __package__)
Endpoint = _legacy.Endpoint

__all__ = ["Endpoint", "Extractor"]


def __getattr__(name: str) -> Any:
    return getattr(_legacy, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_legacy)))
