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
from typing import Any, cast

from loguru import logger

from .dataset_manifest import write_dataset_manifest
from .dataset_registry import DatasetRegistry
from .dataset_resolver import ResolvedDataset, resolve_dataset
from .fundamentals import PIT_FIELDS
from .lineage import git_revision, resolve_qlib_repo, sha256_json
from .settings import Settings
from .store import sha256_file
from .universe import install_qlib_universe, membership_fingerprint


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
    export_cfg = settings.data.get("qlib", {}).get("export", {})
    configured_workers = export_cfg.get("max_workers", 4) if isinstance(export_cfg, dict) else 4
    max_workers = 1 if single_thread else max(1, int(configured_workers))
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


def smoke_test_dataset(dataset_dir: Path, instruments_name: str = "all") -> dict[str, object]:
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
    start = calendar[max(0, len(calendar) - 5)]
    end = calendar[-1]
    instruments = D.list_instruments(
        {"market": instruments_name, "filter_pipe": []},
        start_time=start,
        end_time=end,
        freq="day",
        as_list=True,
    )
    if not instruments:
        raise RuntimeError("Qlib smoke test failed: no active instruments in the terminal window")
    sample = instruments[: min(3, len(instruments))]
    queried_fields = ["$close", "$volume", "$factor", *(f"${field}" for field in PIT_FIELDS)]
    features = D.features(sample, queried_fields, start_time=start, end_time=end, freq="day")
    if features.empty:
        raise RuntimeError("Qlib smoke test failed: feature query returned empty")
    return {
        "calendar_count": len(calendar),
        "instrument_count": len(instruments),
        "instruments_name": instruments_name,
        "sample_instruments": sample,
        "sample_rows": len(features),
        "queried_fields": queried_fields,
        "last_date": str(calendar[-1]),
    }


def _smoke_test_dataset_subprocess(dataset_dir: Path, instruments_name: str = "all") -> dict[str, object]:
    marker = "__TQ_SMOKE_RESULT__="
    script = (
        "import json, sys; from pathlib import Path; "
        "from tushare_qlib.qlib_export import smoke_test_dataset; "
        f"print('{marker}' + json.dumps(smoke_test_dataset(Path(sys.argv[1]), sys.argv[2])))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(dataset_dir), instruments_name],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise RuntimeError(
            f"Qlib smoke test failed for {dataset_dir}: {completed.stderr.strip() or completed.stdout.strip()}"
        )
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith(marker):
            value = json.loads(line[len(marker) :])
            if not isinstance(value, dict):
                raise RuntimeError("Qlib smoke test result must be an object")
            return cast(dict[str, object], value)
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


def _backup_keep(settings: Settings) -> int:
    export_cfg = settings.data.get("qlib", {}).get("export", {})
    keep = int(export_cfg.get("backup_keep", 3)) if isinstance(export_cfg, dict) else 3
    if keep < 0:
        raise ValueError("qlib.export.backup_keep must be non-negative")
    return keep


def _prune_backups(settings: Settings) -> None:
    keep = _backup_keep(settings)
    pattern = f"{settings.qlib_data_uri.name}.backup.*"
    backups = sorted(
        settings.qlib_data_uri.parent.glob(pattern),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for obsolete in backups[keep:]:
        try:
            shutil.rmtree(obsolete)
        except OSError as exc:
            logger.warning("Unable to prune Qlib backup {}: {}", obsolete, type(exc).__name__)


def _clone_base_dataset(source: ResolvedDataset, target: Path) -> None:
    # The Qlib dumper mutates candidate files.  Linking before it runs would
    # mutate the immutable parent through the shared inode.
    shutil.copytree(source.data_path, target)


def _deduplicate_unchanged(parent: ResolvedDataset | None, candidate: Path) -> None:
    if parent is None or parent.reference == "legacy":
        return
    for candidate_file in sorted(item for item in candidate.rglob("*") if item.is_file()):
        relative = candidate_file.relative_to(candidate)
        if relative.as_posix() == "dataset_manifest.json":
            continue
        parent_file = parent.data_path / relative
        if (
            not parent_file.is_file()
            or parent_file.stat().st_size != candidate_file.stat().st_size
            or sha256_file(parent_file) != sha256_file(candidate_file)
        ):
            continue
        linked = candidate_file.with_name(f".{candidate_file.name}.linked.{os.getpid()}")
        try:
            os.link(parent_file, linked)
            os.replace(linked, candidate_file)
        except OSError:
            linked.unlink(missing_ok=True)


def _publish_candidate(
    settings: Settings,
    candidate: Path,
    *,
    mode: str,
    smoke: dict[str, object],
    sync_context: dict[str, object] | None,
    parent: ResolvedDataset | None = None,
) -> Path:
    _deduplicate_unchanged(parent, candidate)
    manifest_path = write_fingerprint(
        settings,
        mode=mode,
        smoke=smoke,
        dataset_dir=candidate,
        sync_context=sync_context,
        parent=parent,
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    version_id = str(payload["version_id"])
    final = settings.qlib_versions_root / version_id
    payload["data_path"] = str(final.resolve())
    payload["status"] = "PUBLISHED"
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if final.exists():
        existing = final / "dataset_manifest.json"
        if (
            not existing.is_file()
            or json.loads(existing.read_text(encoding="utf-8")).get("version_id") != version_id
        ):
            raise FileExistsError(f"Qlib version path collision: {final}")
        shutil.rmtree(candidate)
    else:
        os.replace(candidate, final)
    registry = DatasetRegistry(settings.registry_path)
    registry.initialize()
    final_manifest = final / "dataset_manifest.json"
    registered = registry.register_dataset(
        json.loads(final_manifest.read_text(encoding="utf-8")), final_manifest
    )
    registry.promote(settings.qlib_dataset_ref, registered.version_id)
    logger.info(
        "Published immutable Qlib dataset: alias={}, version={}", settings.qlib_dataset_ref, version_id
    )
    return final


def dump_full(
    settings: Settings,
    *,
    single_thread: bool = False,
    sync_context: dict[str, object] | None = None,
) -> Path:
    versions = settings.qlib_versions_root
    versions.mkdir(parents=True, exist_ok=True)
    candidate = Path(tempfile.mkdtemp(prefix=".building.", dir=versions))
    try:
        _run(settings, "dump_all", settings.paths.staging_full, candidate, single_thread=single_thread)
        install_qlib_universe(settings, candidate)
        instruments_name = str(settings.data.get("universe", {}).get("instruments", "all"))
        smoke = _smoke_test_dataset_subprocess(candidate, instruments_name)
        return _publish_candidate(settings, candidate, mode="full", smoke=smoke, sync_context=sync_context)
    except Exception:
        shutil.rmtree(candidate, ignore_errors=True)
        raise


def dump_update(
    settings: Settings,
    *,
    single_thread: bool = False,
    sync_context: dict[str, object] | None = None,
) -> Path:
    base = resolve_dataset(settings)
    if not base.data_path.exists():
        raise FileNotFoundError(f"base Qlib dataset not found: {base.data_path}")
    export_cfg = settings.data.get("qlib", {}).get("export", {})
    copy_on_write = (
        bool(export_cfg.get("copy_on_write_update", True)) if isinstance(export_cfg, dict) else True
    )
    if not copy_on_write:
        raise RuntimeError(
            "In-place dump_update is disabled by the commercial baseline. "
            "Set qlib.export.copy_on_write_update=true."
        )
    versions = settings.qlib_versions_root
    versions.mkdir(parents=True, exist_ok=True)
    candidate = versions / f".update.{os.getpid()}"
    if candidate.exists():
        shutil.rmtree(candidate)
    _clone_base_dataset(base, candidate)
    try:
        _run(settings, "dump_update", settings.paths.staging_update, candidate, single_thread=single_thread)
        install_qlib_universe(settings, candidate)
        instruments_name = str(settings.data.get("universe", {}).get("instruments", "all"))
        smoke = _smoke_test_dataset_subprocess(candidate, instruments_name)
        return _publish_candidate(
            settings,
            candidate,
            mode="update",
            smoke=smoke,
            sync_context=sync_context,
            parent=base,
        )
    except Exception:
        shutil.rmtree(candidate, ignore_errors=True)
        raise


def dump_update_and_fix(
    settings: Settings,
    *,
    append: bool,
    repair: bool,
    single_thread: bool = False,
    sync_context: dict[str, object] | None = None,
) -> Path:
    """Publish appended dates and historical symbol repairs in one candidate."""

    if not append and not repair:
        raise ValueError("dump_update_and_fix requires append or repair")
    base = resolve_dataset(settings)
    if not base.data_path.is_dir():
        raise FileNotFoundError(f"base Qlib dataset not found: {base.data_path}")
    versions = settings.qlib_versions_root
    versions.mkdir(parents=True, exist_ok=True)
    candidate = versions / f".daily-sync.{os.getpid()}"
    if candidate.exists():
        shutil.rmtree(candidate)
    _clone_base_dataset(base, candidate)
    try:
        if append:
            _run(
                settings, "dump_update", settings.paths.staging_update, candidate, single_thread=single_thread
            )
        if repair:
            _run(settings, "dump_fix", settings.paths.staging_repair, candidate, single_thread=single_thread)
        install_qlib_universe(settings, candidate)
        instruments_name = str(settings.data.get("universe", {}).get("instruments", "all"))
        smoke = _smoke_test_dataset_subprocess(candidate, instruments_name)
        mode = "update_fix" if append and repair else ("update" if append else "repair")
        return _publish_candidate(
            settings,
            candidate,
            mode=mode,
            smoke=smoke,
            sync_context=sync_context,
            parent=base,
        )
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


def write_fingerprint(
    settings: Settings,
    *,
    mode: str,
    smoke: dict[str, object],
    dataset_dir: Path | None = None,
    sync_context: dict[str, object] | None = None,
    parent: ResolvedDataset | None = None,
) -> Path:
    if mode == "full":
        stage_manifests = [settings.paths.staging_full / "staging_manifest.json"]
    elif mode == "repair":
        stage_manifests = [settings.paths.staging_repair / "staging_manifest.json"]
    elif mode == "update_fix":
        stage_manifests = [
            settings.paths.staging_update / "staging_manifest.json",
            settings.paths.staging_repair / "staging_manifest.json",
        ]
    else:
        stage_manifests = [settings.paths.staging_update / "staging_manifest.json"]
    stage_hashes = {
        path.parent.name: sha256_json(_read_staging_manifest(path.parent).get("files", {}))
        for path in stage_manifests
    }
    combined_stage_hash = sha256_json(stage_hashes)
    platform_git = git_revision(Path(__file__).resolve().parents[2])
    qlib_git = git_revision(resolve_qlib_repo(settings.qlib_repo))
    universe_hash = membership_fingerprint(settings)
    content: dict[str, object] = {
        "dataset_id": settings.data["qlib"].get("dataset_version", settings.qlib_data_uri.name),
        "fields": settings.data["qlib"]["include_fields"],
        "staging_manifest_sha256": combined_stage_hash,
        "staging_manifests": stage_hashes,
        "pipeline_config_sha256": sha256_file(settings.config_path),
        "universe_membership_sha256": universe_hash,
        "qlib_platform_git_commit": platform_git.get("commit"),
        "qlib_git_commit": qlib_git.get("commit"),
        "package_versions": _package_versions(),
        "smoke_test": smoke,
    }
    if settings.uses_platform_release():
        release_cfg = settings.platform_release_config
        content["data_release_id"] = str(release_cfg.get("id") or "")
        if isinstance(sync_context, dict) and sync_context.get("data_release_manifest_sha256"):
            content["data_release_manifest_sha256"] = str(sync_context["data_release_manifest_sha256"])
    target = dataset_dir or settings.qlib_data_uri
    calendar = target / "calendars" / "day.txt"
    dates = (
        [line.strip() for line in calendar.read_text(encoding="utf-8").splitlines() if line.strip()]
        if calendar.is_file()
        else []
    )
    parents: list[dict[str, object]] = (
        [{"version_id": parent.version_id, "relation": "updated_from"}]
        if parent is not None and parent.reference != "legacy"
        else []
    )
    if isinstance(sync_context, dict):
        configured_parents = sync_context.get("dataset_parents", [])
        configured_parents = configured_parents if isinstance(configured_parents, list) else []
        for item in configured_parents:
            if isinstance(item, dict) and item.get("version_id"):
                parents.append(
                    {
                        "version_id": str(item["version_id"]),
                        "relation": str(item.get("relation", "converted_from")),
                    }
                )
    semantic_contract = {
        **content,
        "adjustment_policy": "stable_total_return_first_valid_anchor",
        "pit_availability_policy": "next_trading_day",
    }
    path, payload = write_dataset_manifest(
        target,
        dataset_name=settings.qlib_dataset_name,
        layer="qlib",
        semantic_contract=semantic_contract,
        parents=parents,
        coverage={"start": dates[0] if dates else None, "end": dates[-1] if dates else None},
        quality={"smoke_test": smoke, "passed": True},
        extra={
            "dataset_id": settings.qlib_dataset_name,
            "mode": mode,
            "fields": settings.data["qlib"]["include_fields"],
            "staging_manifest_sha256": combined_stage_hash,
            "staging_manifests": stage_hashes,
            "source_snapshot_id": combined_stage_hash,
            "universe_membership_sha256": universe_hash,
            "pipeline_config_sha256": sha256_file(settings.config_path),
            "qlib_platform_git_commit": platform_git.get("commit"),
            "qlib_platform_git_dirty": platform_git.get("dirty"),
            "qlib_git_commit": qlib_git.get("commit"),
            "qlib_git_dirty": qlib_git.get("dirty"),
            "package_versions": _package_versions(),
            "smoke_test": smoke,
            "sync_context": sync_context,
        },
    )
    payload["sha256"] = payload["version_id"]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
