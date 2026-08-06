from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from loguru import logger

from .settings import Settings


def _dump_script(settings: Settings) -> Path:
    path = settings.qlib_repo / "scripts" / "dump_bin.py"
    if not path.exists():
        raise FileNotFoundError(
            f"Qlib dump script not found: {path}. Clone microsoft/qlib and set QLIB_REPO."
        )
    return path


def _run(settings: Settings, mode: str, data_path: Path, *, single_thread: bool = False) -> None:
    fields = ",".join(settings.data["qlib"]["include_fields"])
    if single_thread:
        max_workers = 1
    else:
        try:
            max_workers = max(1, int(os.getenv("TUSHARE_QLIB_MAX_WORKERS", "1")))
        except ValueError:
            logger.warning(
                "Invalid TUSHARE_QLIB_MAX_WORKERS value {}. Fallback to 1.",
                os.getenv("TUSHARE_QLIB_MAX_WORKERS"),
            )
            max_workers = 1
    cmd = [
        sys.executable,
        str(_dump_script(settings)),
        mode,
        f"--data_path={data_path}",
        f"--qlib_dir={settings.qlib_data_uri}",
        "--freq=day",
        "--file_suffix=.parquet",
        "--date_field_name=date",
        "--symbol_field_name=symbol",
        f"--include_fields={fields}",
        f"--max_workers={max_workers}",
    ]
    logger.info("Run Qlib dump: {}", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=settings.qlib_repo)


def dump_full(settings: Settings, *, single_thread: bool = False) -> None:
    settings.qlib_data_uri.parent.mkdir(parents=True, exist_ok=True)
    _run(settings, "dump_all", settings.paths.staging_full, single_thread=single_thread)
    write_fingerprint(settings)


def dump_update(settings: Settings, *, single_thread: bool = False) -> None:
    _run(settings, "dump_update", settings.paths.staging_update, single_thread=single_thread)
    write_fingerprint(settings)


def write_fingerprint(settings: Settings) -> Path:
    payload = {
        "dataset_dir": str(settings.qlib_data_uri),
        "fields": settings.data["qlib"]["include_fields"],
        "config": settings.data,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    payload["sha256"] = hashlib.sha256(raw).hexdigest()
    path = settings.qlib_data_uri / "dataset_manifest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
