from __future__ import annotations

import sysconfig
from pathlib import Path


def resource_path(relative: str | Path) -> Path:
    """Resolve a repo resource from a checkout or an installed wheel data directory."""

    requested = Path(relative).expanduser()
    if requested.is_absolute() or requested.exists():
        return requested
    repository = Path(__file__).resolve().parents[2] / requested
    if repository.exists():
        return repository
    installed = Path(sysconfig.get_path("data")) / requested
    if installed.exists():
        return installed
    return requested


def resource_argument(relative: str) -> str:
    if Path(relative).exists():
        return relative
    return str(resource_path(relative))
