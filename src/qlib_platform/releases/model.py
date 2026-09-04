from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from qlib_platform.datasets.data_release import DataRelease


@dataclass(frozen=True)
class ReleaseRecord:
    data_release_id: str
    manifest_path: Path
    profile: str
    manifest_sha256: str


@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    data_release_id: str
    manifest_sha256: str
    profile: str


__all__ = ["DataRelease", "ReleaseRecord", "VerificationResult"]
