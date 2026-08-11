from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from .fundamentals import PIT_FIELDS
from .lineage import git_revision, resolve_qlib_repo, sha256_json
from .settings import Settings
from .store import sha256_file


def _dump_script(settings: Settings) -> Path:
    repo = resolve_qlib_repo(settings.qlib_repo)
    if repo is None:
        raise RuntimeError(
            "QLIB_REPO does not exist and the imported qlib package is not backed by a Git checkout"
        )
    path = repo / "scripts" / "dump_bin.py"
    if not path.exists():
        raise FileNotFoundError(f"Qlib dump script not found: {path}")
    return path


def _read_staging_manifest(data_path: Path) -> dict[str, Any]:
    path = data_path / "staging_manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"staging manifest not found: {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"staging manifest must be a JSON object: {path}")
    files = manifest.get("files", {})
    if not isinstance(files, dict) or not files:
        raise ValueError(f"staging manifest contains no files: {path}")
    for name, expected in files.items():
        file_path = data_path / str(name)
        if not file_path.exists():
            raise FileNotFoundError(f"staging file missing: {file_path}")
        actual = sha256_file(file_path)
        if actual != expected:
            raise ValueError(f"staging file checksum mismatch: {file_path}")
    return dict(manifest)


def _run(
    settings: Settings,
    mode: str,
    data_path: Path,
    qlib_dir: Path,
    *,
    single_thread: bool = False,
) -> None:
    _read_staging_manifest(data_path)
    dump_script = _dump_script(settings)
    qlib_repo = dump_script.parent.parent
    fields = ",".join(settings.data["qlib"]["include_fields"])
    workers_raw = "1" if single_thread else os.getenv("TUSHARE_QLIB_MAX_WORKERS", "1")
    try:
        max_workers = max(1, int(workers_raw))
    except ValueError:
        logger.warning("Invalid TUSHARE_QLIB_MAX_WORKERS={}, fallback to 1", workers_raw)
        max_workers = 1
    cmd = [
        sys.executable,
        str(dump_script),
        mode,
        f"--data_path={data_path}",
        f"--qlib_dir={qlib_dir}",
        "--freq=day",
        "--file_suffix=.parquet",
        "--date_field_name=date",
        "--symbol_field_name=symbol",
        f"--include_fields={fields}",
        f"--max_workers={max_workers}",
    ]
    logger.info("Run Qlib dump: {}", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=qlib_repo)


def smoke_test_dataset(dataset_dir: Path) -> dict[str, object]:
    """Open the produced dataset through Qlib and execute minimal calendar/instrument/feature queries."""
    try:
        import qlib
        from qlib.constant import REG_CN
        from qlib.data import D
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("pyqlib is required for dataset smoke tests") from exc

    qlib.init(provider_uri=str(dataset_dir), region=REG_CN)
    calendar = list(D.calendar(freq="day"))
    if not calendar:
        raise RuntimeError("Qlib smoke test failed: empty calendar")
    instruments = D.list_instruments({"market": "all", "filter_pipe": []}, as_list=True)
    if not instruments:
        raise RuntimeError("Qlib smoke test failed: empty instrument universe")
    sample = instruments[: min(3, len(instruments))]
    start = calendar[max(0, len(calendar) - 5)]
    end = calendar[-1]
    queried_fields = ["$close", "$volume", "$factor", *(f"${field}" for field in PIT_FIELDS)]
    features = D.features(sample, queried_fields, start_time=start, end_time=end, freq="day")
    if features.empty:
        raise RuntimeError("Qlib smoke test failed: feature query returned empty")
    return {
        "calendar_count": len(calendar),
        "instrument_count": len(instruments),
        "sample_instruments": sample,
        "sample_rows": len(features),
        "queried_fields": queried_fields,
        "last_date": str(calendar[-1]),
    }


def _smoke_test_dataset_subprocess(dataset_dir: Path) -> dict[str, object]:
    marker = "__TQ_SMOKE_RESULT__="
    script = (
        "import json, sys; from pathlib import Path; "
        "from tushare_qlib.qlib_export import smoke_test_dataset; "
        f"print('{marker}' + json.dumps(smoke_test_dataset(Path(sys.argv[1]))))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(dataset_dir)], check=False, capture_output=True, text=True
    )
    if completed.returncode:
        raise RuntimeError(
            f"Qlib smoke test failed for {dataset_dir}: {completed.stderr.strip() or completed.stdout.strip()}"
        )
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith(marker):
            return json.loads(line[len(marker) :])
    raise RuntimeError(f"Qlib smoke test returned no result for {dataset_dir}")


def _portable_dataset_dir(settings: Settings) -> str:
    try:
        return settings.qlib_data_uri.relative_to(settings.paths.root).as_posix()
    except ValueError:
        return settings.qlib_data_uri.name


def _replace_directory_atomic(candidate: Path, target: Path) -> Path | None:
    backup: Path | None = None
    if target.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = target.with_name(f"{target.name}.backup.{stamp}")
        os.replace(target, backup)
    try:
        os.replace(candidate, target)
    except Exception:
        if backup is not None and backup.exists() and not target.exists():
            os.replace(backup, target)
        raise
    return backup


def dump_full(settings: Settings, *, single_thread: bool = False) -> Path:
    target = settings.qlib_data_uri
    target.parent.mkdir(parents=True, exist_ok=True)
    candidate = Path(tempfile.mkdtemp(prefix=f".{target.name}.building.", dir=target.parent))
    try:
        _run(settings, "dump_all", settings.paths.staging_full, candidate, single_thread=single_thread)
        smoke = _smoke_test_dataset_subprocess(candidate)
        backup = _replace_directory_atomic(candidate, target)
        write_fingerprint(settings, mode="full", smoke=smoke)
        logger.info("Promoted Qlib dataset: {}, backup={}", target, backup)
        return target
    except Exception:
        shutil.rmtree(candidate, ignore_errors=True)
        raise


def dump_update(settings: Settings, *, single_thread: bool = False) -> Path:
    target = settings.qlib_data_uri
    if not target.exists():
        raise FileNotFoundError(f"base Qlib dataset not found: {target}")
    export_cfg = settings.data.get("qlib", {}).get("export", {})
    copy_on_write = (
        bool(export_cfg.get("copy_on_write_update", True)) if isinstance(export_cfg, dict) else True
    )
    if not copy_on_write:
        raise RuntimeError(
            "In-place dump_update is disabled by the commercial baseline. "
            "Set qlib.export.copy_on_write_update=true."
        )
    candidate = target.with_name(f".{target.name}.update.{os.getpid()}")
    if candidate.exists():
        shutil.rmtree(candidate)
    shutil.copytree(target, candidate)
    try:
        _run(settings, "dump_update", settings.paths.staging_update, candidate, single_thread=single_thread)
        smoke = _smoke_test_dataset_subprocess(candidate)
        backup = _replace_directory_atomic(candidate, target)
        write_fingerprint(settings, mode="update", smoke=smoke)
        logger.info("Promoted Qlib update: {}, backup={}", target, backup)
        return target
    except Exception:
        shutil.rmtree(candidate, ignore_errors=True)
        raise


def _package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in ("pyqlib", "pandas", "numpy", "pyarrow", "duckdb", "tushare", "lightgbm"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not-installed"
    return versions


def write_fingerprint(settings: Settings, *, mode: str, smoke: dict[str, object]) -> Path:
    stage = settings.paths.staging_full if mode == "full" else settings.paths.staging_update
    stage_manifest = stage / "staging_manifest.json"
    platform_git = git_revision(Path(__file__).resolve().parents[2])
    qlib_git = git_revision(resolve_qlib_repo(settings.qlib_repo))
    content: dict[str, object] = {
        "dataset_id": settings.data["qlib"].get("dataset_version", settings.qlib_data_uri.name),
        "mode": mode,
        "fields": settings.data["qlib"]["include_fields"],
        "staging_manifest_sha256": sha256_file(stage_manifest),
        "pipeline_config_sha256": sha256_file(settings.config_path),
        "qlib_platform_git_commit": platform_git.get("commit"),
        "qlib_git_commit": qlib_git.get("commit"),
        "package_versions": _package_versions(),
        "smoke_test": smoke,
    }
    content_hash = sha256_json(content)
    generated_at = datetime.now(timezone.utc)
    payload: dict[str, object] = {
        "schema_version": "2.0",
        "dataset_id": settings.data["qlib"].get("dataset_version", settings.qlib_data_uri.name),
        "dataset_build_id": f"{generated_at.strftime('%Y%m%dT%H%M%SZ')}-{content_hash[:12]}",
        "dataset_dir": str(settings.qlib_data_uri),
        "mode": mode,
        "generated_at_utc": generated_at.isoformat(),
        "fields": settings.data["qlib"]["include_fields"],
        "staging_manifest_sha256": sha256_file(stage_manifest),
        "source_snapshot_id": sha256_file(stage_manifest),
        "pipeline_config_sha256": sha256_file(settings.config_path),
        "qlib_platform_git_commit": platform_git.get("commit"),
        "qlib_platform_git_dirty": platform_git.get("dirty"),
        "qlib_git_commit": qlib_git.get("commit"),
        "qlib_git_dirty": qlib_git.get("dirty"),
        "package_versions": _package_versions(),
        "smoke_test": smoke,
        "content": content,
        "sha256": content_hash,
    }
    path = settings.qlib_data_uri / "dataset_manifest.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path
