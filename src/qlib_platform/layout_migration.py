from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .dataset_manifest import write_dataset_manifest
from .dataset_registry import DatasetRegistry
from .settings import Settings
from .store import sha256_file


def _tree_stats(path: Path) -> tuple[int, int]:
    files = [item for item in path.rglob("*") if item.is_file()] if path.is_dir() else []
    return len(files), sum(item.stat().st_size for item in files)


def _clone_immutable_tree(source: Path, target: Path) -> None:
    def link_or_copy(src: str, dst: str) -> str:
        try:
            os.link(src, dst)
            return dst
        except OSError:
            return shutil.copy2(src, dst)

    shutil.copytree(source, target, copy_function=link_or_copy)


def _verify_clone(source: Path, target: Path) -> tuple[int, int]:
    """Verify that *target* is an exact, byte-identical materialization of *source*."""
    source_files = 0
    source_bytes = 0
    for source_file in source.rglob("*"):
        if not source_file.is_file():
            continue
        source_files += 1
        source_size = source_file.stat().st_size
        source_bytes += source_size
        target_file = target / source_file.relative_to(source)
        if not target_file.is_file():
            raise FileNotFoundError(f"migration target is missing file: {target_file}")
        if target_file.stat().st_size != source_size:
            raise OSError(f"migration size mismatch: {source_file} != {target_file}")
        try:
            same_file = os.path.samefile(source_file, target_file)
        except OSError:
            same_file = False
        if not same_file and sha256_file(source_file) != sha256_file(target_file):
            raise OSError(f"migration checksum mismatch: {source_file} != {target_file}")
    target_files = sum(1 for item in target.rglob("*") if item.is_file())
    if target_files != source_files:
        raise OSError(f"migration file-count mismatch: source={source_files}, target={target_files}")
    return source_files, source_bytes


def _materialize_preserving_source(source: Path, target: Path) -> tuple[int, int]:
    """Create an atomic target tree while leaving the source path untouched."""
    if target.exists():
        return _verify_clone(source, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    candidate = target.parent / f".{target.name}.migrating.{os.getpid()}.{uuid.uuid4().hex[:8]}"
    try:
        _clone_immutable_tree(source, candidate)
        stats = _verify_clone(source, candidate)
        os.replace(candidate, target)
        return stats
    except Exception:
        if candidate.exists():
            shutil.rmtree(candidate)
        raise


class LayoutMigrator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.paths.root

    def plan(self) -> dict[str, Any]:
        mappings = self._mappings()
        entries = []
        total_files = 0
        total_bytes = 0
        for source, target in mappings:
            count, size = _tree_stats(source) if source.exists() else (0, 0)
            total_files += count
            total_bytes += size
            entries.append(
                {
                    "source": str(source),
                    "target": str(target),
                    "exists": source.exists(),
                    "target_exists": target.exists(),
                    "files": count,
                    "bytes": size,
                }
            )
        for qlib_source in self._legacy_qlib_sources():
            count, size = _tree_stats(qlib_source)
            total_files += count
            total_bytes += size
            entries.append(
                {
                    "source": str(qlib_source),
                    "target": str(self.settings.qlib_versions_root),
                    "exists": True,
                    "target_exists": False,
                    "files": count,
                    "bytes": size,
                    "kind": "legacy_qlib_import",
                }
            )
        return {
            "schema_version": "1.0",
            "dry_run": True,
            "root": str(self.root),
            "entries": entries,
            "total_files": total_files,
            "total_bytes": total_bytes,
            "free_bytes": shutil.disk_usage(self.root).free,
        }

    def apply(self, migration_id: str | None = None) -> Path:
        migration_id = migration_id or f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
        run_dir = self.settings.paths.migration / migration_id
        journal = run_dir / "journal.json"
        if journal.exists():
            payload = json.loads(journal.read_text(encoding="utf-8"))
            if payload.get("status") == "completed":
                return journal
        else:
            run_dir.mkdir(parents=True, exist_ok=False)
            payload = {
                "schema_version": "1.0",
                "migration_id": migration_id,
                "status": "running",
                "started_at_utc": datetime.now(timezone.utc).isoformat(),
                "steps": [],
            }
            self._write_journal(journal, payload)

        plan = self.plan()
        required = int(plan["total_bytes"] * 1.05)
        if int(plan["free_bytes"]) < required:
            raise OSError(
                f"insufficient free space for worst-case migration: required={required}, "
                f"available={plan['free_bytes']}"
            )
        try:
            for source, target in self._mappings():
                if target.exists():
                    if not source.exists():
                        raise FileNotFoundError(
                            f"cannot verify existing migration target because source is missing: {source}"
                        )
                elif not source.exists():
                    continue
                files, size = _materialize_preserving_source(source, target)
                payload["steps"].append(
                    {
                        "source": str(source),
                        "target": str(target),
                        "source_preserved": True,
                        "verified_files": files,
                        "verified_bytes": size,
                        "status": "done",
                    }
                )
                self._write_journal(journal, payload)
            imported = self._import_legacy_qlib()
            if imported:
                payload["legacy_qlib_versions"] = imported
            payload["legacy_research_records"] = self._index_legacy_research()
            payload["status"] = "completed"
            payload["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
            self._write_journal(journal, payload)
            return journal
        except Exception as exc:
            payload["status"] = "failed"
            payload["error"] = f"{type(exc).__name__}: {exc}"
            payload["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
            self._write_journal(journal, payload)
            raise

    def _mappings(self) -> list[tuple[Path, Path]]:
        return [
            (self.root / "raw", self.settings.paths.raw),
            (self.root / "curated" / "daily", self.settings.paths.curated),
            (self.root / "metadata", self.settings.paths.metadata),
            (self.root / "staging" / "full", self.settings.paths.staging_full),
            (self.root / "staging" / "update", self.settings.paths.staging_update),
            (self.root / "staging" / "repair", self.settings.paths.staging_repair),
        ]

    def _legacy_qlib_source(self) -> Path:
        return (
            self.root
            / "qlib"
            / str(self.settings.data.get("qlib", {}).get("dataset_version", self.settings.qlib_dataset_name))
        )

    def _legacy_qlib_sources(self) -> list[Path]:
        primary = self._legacy_qlib_source()
        backups = sorted(primary.parent.glob(f"{primary.name}.backup.*")) if primary.parent.is_dir() else []
        return [path for path in (primary, *backups) if path.is_dir()]

    def _import_one_legacy_qlib(self, source: Path) -> str:
        versions = self.settings.qlib_versions_root
        versions.mkdir(parents=True, exist_ok=True)
        candidate = versions / f".legacy-import.{os.getpid()}.{uuid.uuid4().hex[:8]}"
        if candidate.exists():
            shutil.rmtree(candidate)
        _clone_immutable_tree(source, candidate)
        _verify_clone(source, candidate)
        old_manifest = source / "dataset_manifest.json"
        legacy_hash = sha256_file(old_manifest) if old_manifest.is_file() else None
        path, payload = write_dataset_manifest(
            candidate,
            dataset_name=self.settings.qlib_dataset_name,
            layer="qlib",
            semantic_contract={
                "legacy_manifest_sha256": legacy_hash,
                "legacy_source_name": source.name,
                "migration_only": True,
            },
            quality={"passed": False, "reason": "legacy_lineage_requires_v3_rebuild"},
        )
        version_id = str(payload["version_id"])
        final = versions / version_id
        payload["data_path"] = str(final.resolve())
        payload["status"] = "QUARANTINED"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if final.exists():
            shutil.rmtree(candidate)
        else:
            os.replace(candidate, final)
        registry = DatasetRegistry(self.settings.registry_path)
        registry.initialize()
        registry.register_dataset(payload, final / "dataset_manifest.json")
        return version_id

    def _import_legacy_qlib(self) -> list[str]:
        versions: list[str] = []
        for source in self._legacy_qlib_sources():
            versions.append(self._import_one_legacy_qlib(source))
        return versions

    def _index_legacy_research(self) -> int:
        registry = DatasetRegistry(self.settings.registry_path)
        count = 0
        for manifest in sorted((self.settings.paths.output / "research").glob("*/manifest.json")):
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            run_id = str(payload.get("externalRunId") or manifest.parent.name)
            registry.register_legacy_record(
                record_id=f"research:{run_id}",
                record_kind="research_run",
                status="LEGACY_UNRESOLVED",
                source_path=manifest,
                metadata={"external_run_id": run_id, "schema_version": payload.get("schemaVersion")},
            )
            count += 1
        return count

    @staticmethod
    def _write_journal(path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
