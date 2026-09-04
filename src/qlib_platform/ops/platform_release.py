"""Backward-compatible PlatformRelease API.

DataRelease v2 is repository-neutral.  Keep wrappers instead of aliases so
existing monkeypatch targets and imports remain stable during migration.
"""

from __future__ import annotations

from typing import Any

from qlib_platform.datasets.data_release import (
    CORE_RESEARCH_COMPONENTS,
    DATA_RELEASE_PROFILES,
    PROFILE_COMPONENT_SCHEMAS,
    QLIB_RESEARCH_PROFILE,
    QLIB_RESEARCH_PROFILE_V2,
    REQUIRED_RESEARCH_COMPONENTS,
    DataRelease,
    data_release_preflight,
    load_data_release,
    materialize_data_release,
)
from qlib_platform.settings import Settings

PlatformRelease = DataRelease


def load_platform_release(settings: Settings) -> PlatformRelease:
    return load_data_release(settings)


def platform_release_preflight(settings: Settings, start: str, end: str) -> dict[str, Any]:
    return data_release_preflight(settings, start, end)


def materialize_platform_release(settings: Settings) -> PlatformRelease:
    return materialize_data_release(settings)


__all__ = [
    "CORE_RESEARCH_COMPONENTS",
    "DATA_RELEASE_PROFILES",
    "PROFILE_COMPONENT_SCHEMAS",
    "QLIB_RESEARCH_PROFILE",
    "QLIB_RESEARCH_PROFILE_V2",
    "REQUIRED_RESEARCH_COMPONENTS",
    "PlatformRelease",
    "load_platform_release",
    "materialize_platform_release",
    "platform_release_preflight",
]
