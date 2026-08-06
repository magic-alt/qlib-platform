from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

import pandas as pd


class PartitionStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def partition_dir(self, dataset: str, trade_date: str) -> Path:
        return self.root / dataset / f"trade_date={trade_date}"

    def data_path(self, dataset: str, trade_date: str) -> Path:
        return self.partition_dir(dataset, trade_date) / "data.parquet"

    def exists(self, dataset: str, trade_date: str) -> bool:
        return self.data_path(dataset, trade_date).exists()

    def write(self, dataset: str, trade_date: str, df: pd.DataFrame, metadata: dict | None = None) -> Path:
        target_dir = self.partition_dir(dataset, trade_date)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "data.parquet"
        tmp = target.with_suffix(".parquet.tmp")
        # Empty frames are still persisted with their schema when columns exist.
        df.to_parquet(tmp, index=False)
        os.replace(tmp, target)
        meta = {"rows": int(len(df)), "columns": list(df.columns)}
        if metadata:
            meta.update(metadata)
        (target_dir / "manifest.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

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
