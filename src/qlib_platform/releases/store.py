from __future__ import annotations

from typing import Protocol, Sequence

from .model import DataRelease, ReleaseRecord, VerificationResult


class ReleaseStore(Protocol):
    def list(self) -> Sequence[ReleaseRecord]: ...

    def resolve(self, reference: str) -> DataRelease: ...

    def verify(self, release: DataRelease) -> VerificationResult: ...
