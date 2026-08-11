from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

_TERMINAL_STATUSES = {"success", "empty", "permission_denied", "disabled"}


def _atomic_replace(src: Path, dst: Path, retries: int = 3) -> None:
    """Atomic replace with fallback retry for macOS APFS race conditions."""
    if not src.exists():
        raise FileNotFoundError(f"Source file does not exist: {src}")
    for attempt in range(retries):
        try:
            os.replace(src, dst)
            return
        except FileNotFoundError:
            if attempt < retries - 1:
                time.sleep(0.1)
                if not src.exists():
                    raise
                continue
            # Fallback: copy then remove
            shutil.copy2(src, dst)
            if src.exists():
                src.unlink()
            return


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        while chunk := fp.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def frame_content_sha256(frame: pd.DataFrame, *, key_columns: Iterable[str] = ()) -> str:
    """Hash logical frame content independently from source row ordering."""

    columns = sorted(str(column) for column in frame.columns)
    canonical = frame.loc[:, columns].copy()
    keys = [column for column in key_columns if column in canonical]
    sort_columns = keys or columns
    if sort_columns and not canonical.empty:
        canonical = canonical.sort_values(sort_columns, kind="stable", na_position="first")
    canonical = canonical.reset_index(drop=True)
    schema = json.dumps(
        [(column, str(canonical[column].dtype)) for column in columns],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    row_hashes = pd.util.hash_pandas_object(canonical, index=False, categorize=False).to_numpy()
    digest = hashlib.sha256(schema)
    digest.update(row_hashes.tobytes())
    return digest.hexdigest()


class PartitionStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def partition_dir(self, dataset: str, trade_date: str) -> Path:
        return self.root / dataset / f"trade_date={trade_date}"

    def data_path(self, dataset: str, trade_date: str) -> Path:
        return self.partition_dir(dataset, trade_date) / "data.parquet"

    def manifest_path(self, dataset: str, trade_date: str) -> Path:
        return self.partition_dir(dataset, trade_date) / "manifest.json"

    def exists(self, dataset: str, trade_date: str) -> bool:
        return self.data_path(dataset, trade_date).exists()

    def read_manifest(self, dataset: str, trade_date: str) -> dict[str, Any]:
        path = self.manifest_path(dataset, trade_date)
        if not path.exists():
            return {}
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}

    def is_terminal(self, dataset: str, trade_date: str) -> bool:
        manifest = self.read_manifest(dataset, trade_date)
        status = str(manifest.get("status", ""))
        if status in {"success", "empty"}:
            return self.data_path(dataset, trade_date).exists()
        return status in _TERMINAL_STATUSES

    def write(
        self,
        dataset: str,
        trade_date: str,
        df: pd.DataFrame,
        metadata: dict[str, Any] | None = None,
        *,
        status: str | None = None,
    ) -> Path:
        target_dir = self.partition_dir(dataset, trade_date)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "data.parquet"
        tmp = target.with_suffix(".parquet.tmp")
        for _write_attempt in range(3):
            df.to_parquet(tmp, index=False)
            if tmp.exists():
                break
            time.sleep(0.1)
        else:
            # Direct write fallback if tmp file keeps disappearing
            df.to_parquet(target, index=False)
            actual_status = status or ("empty" if df.empty else "success")
            fallback_meta: dict[str, Any] = {
                "dataset": dataset,
                "trade_date": trade_date,
                "status": actual_status,
                "rows": int(len(df)),
                "columns": [str(c) for c in df.columns],
                "bytes": target.stat().st_size,
                "sha256": sha256_file(target),
                "written_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            if metadata:
                fallback_meta.update(metadata)
            self._write_manifest(dataset, trade_date, fallback_meta)
            return target
        _atomic_replace(tmp, target)
        actual_status = status or ("empty" if df.empty else "success")
        meta: dict[str, Any] = {
            "dataset": dataset,
            "trade_date": trade_date,
            "status": actual_status,
            "rows": int(len(df)),
            "columns": [str(c) for c in df.columns],
            "bytes": target.stat().st_size,
            "sha256": sha256_file(target),
            "written_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        if metadata:
            meta.update(metadata)
        self._write_manifest(dataset, trade_date, meta)
        return target

    def write_if_changed(
        self,
        dataset: str,
        trade_date: str,
        df: pd.DataFrame,
        metadata: dict[str, Any] | None = None,
        *,
        key_columns: Iterable[str] = ("ts_code", "trade_date"),
        revision_root: Path | None = None,
        status: str | None = None,
    ) -> tuple[Path, bool, str]:
        """Promote a logical partition and content-address the prior revision."""

        logical_hash = frame_content_sha256(df, key_columns=key_columns)
        current_manifest = self.read_manifest(dataset, trade_date)
        current_hash = str(current_manifest.get("content_sha256", ""))
        if not current_hash and self.exists(dataset, trade_date):
            current_hash = frame_content_sha256(self.read(dataset, trade_date), key_columns=key_columns)
        if current_hash == logical_hash:
            return self.data_path(dataset, trade_date), False, logical_hash

        if revision_root is not None and self.exists(dataset, trade_date):
            archive = Path(revision_root) / dataset / f"trade_date={trade_date}" / current_hash
            archive.mkdir(parents=True, exist_ok=True)
            archived_data = archive / "data.parquet"
            archived_manifest = archive / "manifest.json"
            if not archived_data.exists():
                shutil.copy2(self.data_path(dataset, trade_date), archived_data)
            manifest_path = self.manifest_path(dataset, trade_date)
            if manifest_path.exists() and not archived_manifest.exists():
                shutil.copy2(manifest_path, archived_manifest)

        promoted_metadata = dict(metadata or {})
        promoted_metadata["content_sha256"] = logical_hash
        return (
            self.write(dataset, trade_date, df, promoted_metadata, status=status),
            True,
            logical_hash,
        )

    def write_status(
        self,
        dataset: str,
        trade_date: str,
        *,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        target_dir = self.partition_dir(dataset, trade_date)
        target_dir.mkdir(parents=True, exist_ok=True)
        data_path = self.data_path(dataset, trade_date)
        if data_path.exists() and status not in {"success", "empty"}:
            data_path.unlink()
        meta: dict[str, Any] = {
            "dataset": dataset,
            "trade_date": trade_date,
            "status": status,
            "rows": 0,
            "columns": [],
            "written_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        if metadata:
            meta.update(metadata)
        return self._write_manifest(dataset, trade_date, meta)

    def _write_manifest(self, dataset: str, trade_date: str, meta: dict[str, Any]) -> Path:
        path = self.manifest_path(dataset, trade_date)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        _atomic_replace(tmp, path)
        return path

    def read(self, dataset: str, trade_date: str) -> pd.DataFrame:
        path = self.data_path(dataset, trade_date)
        if not path.exists():
            return pd.DataFrame()
        return pd.read_parquet(path)

    def list_dates(self, dataset: str) -> list[str]:
        base = self.root / dataset
        if not base.exists():
            return []
        return sorted(p.name.split("=", 1)[1] for p in base.glob("trade_date=*") if p.is_dir())

    def files(self, dataset: str) -> Iterable[Path]:
        for trade_date in self.list_dates(dataset):
            path = self.data_path(dataset, trade_date)
            if path.exists():
                yield path
