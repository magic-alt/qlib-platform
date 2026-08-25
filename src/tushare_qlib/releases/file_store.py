from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Sequence

from ..data_release import verify_data_release
from .model import DataRelease, ReleaseRecord, VerificationResult


_RELEASE_ID = re.compile(r"ds_[a-f0-9]{64}")


class FileReleaseStore:
    """Content-addressed local DataRelease v2 store."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

    def _resolve_reference(self, reference: str) -> str:
        if _RELEASE_ID.fullmatch(reference):
            return reference
        if not reference or any(token in reference for token in ("/", "\\", "..")):
            raise ValueError(f"invalid DataRelease reference: {reference!r}")
        pointer = self.root / "aliases" / f"{reference}.json"
        if pointer.is_symlink():
            raise ValueError("DataRelease alias must not be a symlink")
        if not pointer.is_file():
            raise KeyError(f"unknown DataRelease reference: {reference}")
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        release_id = str(payload.get("dataReleaseId") or "")
        if not _RELEASE_ID.fullmatch(release_id):
            raise ValueError(f"invalid DataRelease alias target: {reference}")
        return release_id

    def resolve(
        self,
        reference: str,
        *,
        mode: str = "manifest",
        receipt_dir: str | Path | None = None,
        reuse_receipt: bool = False,
        sample_size: int = 64,
        evidence: dict[str, object] | None = None,
        workers: int = 1,
    ) -> DataRelease:
        release_id = self._resolve_reference(reference)
        manifest = self.root / release_id / "manifest.json"
        return verify_data_release(
            self.root,
            manifest,
            configured_id=release_id,
            mode=mode,
            receipt_dir=receipt_dir,
            reuse_receipt=reuse_receipt,
            sample_size=sample_size,
            evidence=evidence,
            workers=workers,
        )

    def list(self) -> Sequence[ReleaseRecord]:
        if not self.root.is_dir():
            return ()
        records: list[ReleaseRecord] = []
        for path in sorted(self.root.glob("ds_*/manifest.json")):
            release = verify_data_release(self.root, path, mode="manifest")
            records.append(
                ReleaseRecord(
                    release.data_release_id,
                    release.manifest_path,
                    release.profile,
                    release.manifest_sha256,
                )
            )
        return tuple(records)

    def verify(self, release: DataRelease) -> VerificationResult:
        verified = verify_data_release(
            release.data_root,
            release.manifest_path,
            configured_id=release.data_release_id,
            mode="deep",
        )
        return VerificationResult(
            True,
            verified.data_release_id,
            verified.manifest_sha256,
            verified.profile,
        )
