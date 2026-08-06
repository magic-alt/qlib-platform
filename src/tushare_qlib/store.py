from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

_TERMINAL_STATUSES = {"success", "empty", "permission_denied", "disabled"}


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        while chunk := fp.read(chunk_size):
            digest.update(chunk)
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
        df.to_parquet(tmp, index=False)
        os.replace(tmp, target)
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
        os.replace(tmp, path)
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
