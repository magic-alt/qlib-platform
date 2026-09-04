from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from qlib_platform.datasets.data_release import verify_data_release
from qlib_platform.releases.model import DataRelease, ReleaseRecord, VerificationResult


_RELEASE_ID = re.compile(r"ds_[a-f0-9]{64}")


class FileReleaseStore:
    """Content-addressed local DataRelease v2 store.

    The active store intentionally stays small for standalone research. Historical
    releases may be moved under ``archive/``; exact immutable IDs still resolve from
    there so audit/replay remains possible without exposing a wall of ``ds_*`` IDs to
    the normal quickstart workflow.
    """

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

    def _manifest_path(self, release_id: str) -> Path:
        active = self.root / release_id / "manifest.json"
        if active.is_file():
            return active
        archived = self.root / "archive" / release_id / "manifest.json"
        if archived.is_file():
            return archived
        return active

    def resolve(
        self,
        reference: str,
        *,
        mode: str = "deep",
        receipt_dir: str | Path | None = None,
        reuse_receipt: bool = False,
        sample_size: int = 64,
        evidence: dict[str, object] | None = None,
        workers: int = 1,
    ) -> DataRelease:
        release_id = self._resolve_reference(reference)
        manifest = self._manifest_path(release_id)
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

    @staticmethod
    def _published_sort_key(record: ReleaseRecord) -> tuple[float, int, str]:
        payload = json.loads(record.manifest_path.read_text(encoding="utf-8"))
        raw = str(payload.get("publishedAt") or payload.get("asOfTime") or "").strip()
        published = float("-inf")
        if raw:
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                published = parsed.timestamp()
            except ValueError:
                published = float("-inf")
        return (published, record.manifest_path.stat().st_mtime_ns, record.data_release_id)

    def list(self) -> Sequence[ReleaseRecord]:
        """List active releases only; archived immutable releases remain ID-addressable."""

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

    def latest(self) -> ReleaseRecord | None:
        records = tuple(self.list())
        return max(records, key=self._published_sort_key) if records else None

    def archive_except(self, keep_release_id: str) -> tuple[str, ...]:
        """Keep one release active while preserving older immutable releases for replay."""

        if not _RELEASE_ID.fullmatch(keep_release_id):
            raise ValueError(f"invalid DataRelease ID: {keep_release_id!r}")
        if not self.root.is_dir():
            return ()
        archived_root = self.root / "archive"
        archived: list[str] = []
        for path in sorted(self.root.glob("ds_*")):
            if not path.is_dir() or path.name == keep_release_id:
                continue
            release = verify_data_release(self.root, path / "manifest.json", mode="manifest")
            target = archived_root / release.data_release_id
            archived_root.mkdir(parents=True, exist_ok=True)
            if target.exists():
                existing = verify_data_release(self.root, target / "manifest.json", mode="manifest")
                if existing.manifest_sha256 != release.manifest_sha256:
                    raise FileExistsError(f"archived DataRelease collision: {release.data_release_id}")
                shutil.rmtree(path)
            else:
                os.replace(path, target)
            archived.append(release.data_release_id)
        return tuple(archived)

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