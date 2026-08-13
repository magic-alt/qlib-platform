from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


DATASET_STATES = {"BUILDING", "VALIDATED", "PUBLISHED", "QUARANTINED"}


@dataclass(frozen=True)
class DatasetVersion:
    version_id: str
    dataset_name: str
    layer: str
    status: str
    manifest_path: Path
    data_path: Path
    created_at_utc: str


class DatasetRegistry:
    """Transactional index for portable dataset manifests."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS dataset_versions (
                    version_id TEXT PRIMARY KEY,
                    dataset_name TEXT NOT NULL,
                    layer TEXT NOT NULL,
                    status TEXT NOT NULL,
                    manifest_path TEXT NOT NULL UNIQUE,
                    data_path TEXT NOT NULL,
                    coverage_start TEXT,
                    coverage_end TEXT,
                    schema_version TEXT NOT NULL,
                    manifest_sha256 TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    CHECK (status IN ('BUILDING','VALIDATED','PUBLISHED','QUARANTINED'))
                );
                CREATE INDEX IF NOT EXISTS idx_dataset_name_created
                    ON dataset_versions(dataset_name, created_at_utc DESC);
                CREATE TABLE IF NOT EXISTS dataset_partitions (
                    version_id TEXT NOT NULL REFERENCES dataset_versions(version_id) ON DELETE CASCADE,
                    partition_key TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    rows_count INTEGER,
                    bytes_count INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    PRIMARY KEY (version_id, partition_key, relative_path)
                );
                CREATE TABLE IF NOT EXISTS dataset_lineage (
                    child_version_id TEXT NOT NULL REFERENCES dataset_versions(version_id) ON DELETE CASCADE,
                    parent_version_id TEXT NOT NULL REFERENCES dataset_versions(version_id),
                    relation TEXT NOT NULL,
                    PRIMARY KEY (child_version_id, parent_version_id, relation)
                );
                CREATE TABLE IF NOT EXISTS dataset_aliases (
                    alias TEXT PRIMARY KEY,
                    dataset_name TEXT NOT NULL,
                    version_id TEXT NOT NULL REFERENCES dataset_versions(version_id),
                    updated_at_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    run_id TEXT PRIMARY KEY,
                    run_kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    dataset_version_id TEXT REFERENCES dataset_versions(version_id),
                    manifest_path TEXT,
                    error_code TEXT,
                    started_at_utc TEXT NOT NULL,
                    finished_at_utc TEXT,
                    CHECK (status IN ('RUNNING','SUCCEEDED','FAILED','PROMOTED','REJECTED'))
                );
                CREATE TABLE IF NOT EXISTS research_runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    dataset_version_id TEXT NOT NULL REFERENCES dataset_versions(version_id),
                    feature_set_id TEXT,
                    model_fingerprint TEXT,
                    mlflow_run_id TEXT,
                    manifest_path TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    CHECK (status IN ('RUNNING','SUCCEEDED','FAILED','PROMOTED','REJECTED'))
                );
                CREATE TABLE IF NOT EXISTS legacy_records (
                    record_id TEXT PRIMARY KEY,
                    record_kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_path TEXT NOT NULL UNIQUE,
                    metadata_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                );
                """
            )

    def register_dataset(self, manifest: Mapping[str, Any], manifest_path: str | Path) -> DatasetVersion:
        status = str(manifest.get("status", "VALIDATED")).upper()
        if status not in DATASET_STATES:
            raise ValueError(f"invalid dataset status: {status}")
        version_id = str(manifest.get("version_id") or manifest.get("sha256") or "")
        if not version_id:
            raise ValueError("dataset manifest has no version_id")
        dataset_name = str(manifest.get("dataset_name") or manifest.get("dataset_id") or "")
        if not dataset_name:
            raise ValueError("dataset manifest has no dataset_name")
        path = Path(manifest_path).expanduser().resolve()
        data_path_raw = manifest.get("data_path") or manifest.get("dataset_dir") or path.parent
        data_path = Path(str(data_path_raw)).expanduser().resolve()
        coverage = manifest.get("coverage", {})
        coverage = coverage if isinstance(coverage, Mapping) else {}
        from .store import sha256_file

        created = str(manifest.get("created_at_utc") or manifest.get("generated_at_utc") or "")
        created = created or datetime.now(timezone.utc).isoformat()
        record = DatasetVersion(
            version_id, dataset_name, str(manifest.get("layer", "qlib")), status, path, data_path, created
        )
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO dataset_versions(
                    version_id,dataset_name,layer,status,manifest_path,data_path,
                    coverage_start,coverage_end,schema_version,manifest_sha256,created_at_utc
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(version_id) DO UPDATE SET status=excluded.status,
                    manifest_path=excluded.manifest_path,data_path=excluded.data_path,
                    manifest_sha256=excluded.manifest_sha256""",
                (
                    version_id,
                    dataset_name,
                    record.layer,
                    status,
                    str(path),
                    str(data_path),
                    coverage.get("start"),
                    coverage.get("end"),
                    str(manifest.get("schema_version", "3.0")),
                    sha256_file(path),
                    created,
                ),
            )
            connection.execute("DELETE FROM dataset_partitions WHERE version_id=?", (version_id,))
            for partition in manifest.get("partitions", []):
                if not isinstance(partition, Mapping):
                    continue
                connection.execute(
                    "INSERT INTO dataset_partitions VALUES(?,?,?,?,?,?)",
                    (
                        version_id,
                        str(partition.get("partition_key", partition.get("path", ""))),
                        str(partition.get("path", "")),
                        partition.get("rows"),
                        int(partition.get("bytes", 0)),
                        str(partition.get("sha256", "")),
                    ),
                )
            for parent in manifest.get("parents", []):
                if not isinstance(parent, Mapping) or not parent.get("version_id"):
                    continue
                parent_id = str(parent["version_id"])
                if (
                    connection.execute(
                        "SELECT 1 FROM dataset_versions WHERE version_id=?", (parent_id,)
                    ).fetchone()
                    is None
                ):
                    continue
                connection.execute(
                    "INSERT OR IGNORE INTO dataset_lineage VALUES(?,?,?)",
                    (version_id, parent_id, str(parent.get("relation", "derived_from"))),
                )
        return record

    def get_version(self, version_id: str) -> DatasetVersion | None:
        self.initialize()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM dataset_versions WHERE version_id=?", (version_id,)
            ).fetchone()
        return self._version(row) if row is not None else None

    def list_versions(self, dataset_name: str | None = None) -> list[DatasetVersion]:
        self.initialize()
        with self.connect() as connection:
            if dataset_name:
                rows = connection.execute(
                    "SELECT * FROM dataset_versions WHERE dataset_name=? ORDER BY created_at_utc DESC",
                    (dataset_name,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM dataset_versions ORDER BY created_at_utc DESC"
                ).fetchall()
        return [self._version(row) for row in rows]

    def promote(self, alias: str, version_id: str) -> DatasetVersion:
        self.initialize()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM dataset_versions WHERE version_id=?", (version_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown dataset version: {version_id}")
            if row["status"] not in {"VALIDATED", "PUBLISHED"}:
                raise ValueError(f"dataset version is not promotable: {row['status']}")
            connection.execute(
                "UPDATE dataset_versions SET status='PUBLISHED' WHERE version_id=?", (version_id,)
            )
            connection.execute(
                """INSERT INTO dataset_aliases(alias,dataset_name,version_id,updated_at_utc)
                   VALUES(?,?,?,?) ON CONFLICT(alias) DO UPDATE SET dataset_name=excluded.dataset_name,
                   version_id=excluded.version_id,updated_at_utc=excluded.updated_at_utc""",
                (alias, row["dataset_name"], version_id, datetime.now(timezone.utc).isoformat()),
            )
            self._write_alias_pointer(alias, str(row["dataset_name"]), version_id)
        resolved = self.get_version(version_id)
        assert resolved is not None
        return resolved

    def resolve(self, reference: str, dataset_name: str | None = None) -> DatasetVersion:
        self.initialize()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT v.* FROM dataset_aliases a JOIN dataset_versions v USING(version_id) WHERE a.alias=?",
                (reference,),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT * FROM dataset_versions WHERE version_id=?", (reference,)
                ).fetchone()
        if row is None:
            raise KeyError(f"unknown dataset reference: {reference}")
        version = self._version(row)
        if dataset_name is not None and version.dataset_name != dataset_name:
            raise ValueError(
                f"dataset reference {reference!r} resolves to {version.dataset_name!r}, expected {dataset_name!r}"
            )
        if version.status != "PUBLISHED":
            raise ValueError(f"dataset reference is not published: {version.version_id}")
        return version

    def rebuild(self, root: str | Path) -> int:
        self.initialize()
        manifests: list[tuple[Path, dict[str, Any]]] = []
        for manifest_path in sorted(Path(root).rglob("dataset_manifest.json")):
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                if str(payload.get("schema_version")) != "3.0":
                    continue
                manifests.append((manifest_path, payload))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        for _pass in range(2):
            for manifest_path, payload in manifests:
                self.register_dataset(payload, manifest_path)
        aliases_root = self.path.parent / "aliases"
        for pointer in aliases_root.glob("*.json") if aliases_root.is_dir() else ():
            try:
                payload = json.loads(pointer.read_text(encoding="utf-8"))
                self.promote(str(payload["alias"]), str(payload["version_id"]))
            except (KeyError, OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return len(manifests)

    def _write_alias_pointer(self, alias: str, dataset_name: str, version_id: str) -> None:
        safe = alias.replace("/", "_").replace("\\", "_")
        if safe != alias or not safe:
            raise ValueError(f"invalid dataset alias: {alias!r}")
        root = self.path.parent / "aliases"
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"{safe}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "alias": alias,
                    "dataset_name": dataset_name,
                    "version_id": version_id,
                    "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.replace(temporary, target)

    def register_research_manifest(self, manifest_path: str | Path) -> bool:
        path = Path(manifest_path).expanduser().resolve()
        manifest = json.loads(path.read_text(encoding="utf-8"))
        dataset = manifest.get("dataset", {})
        dataset = dataset if isinstance(dataset, Mapping) else {}
        version_id = str(dataset.get("versionId") or dataset.get("fingerprint") or "")
        if not version_id or self.get_version(version_id) is None:
            return False
        promotion = manifest.get("promotion", {})
        promotion = promotion if isinstance(promotion, Mapping) else {}
        raw_status = str(promotion.get("status", "SUCCEEDED")).upper()
        status = raw_status if raw_status in {"PROMOTED", "REJECTED"} else "SUCCEEDED"
        feature = manifest.get("featureStore", {})
        feature = feature if isinstance(feature, Mapping) else {}
        model = manifest.get("model", {})
        model = model if isinstance(model, Mapping) else {}
        run_id = str(manifest.get("externalRunId") or path.parent.name)
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO research_runs(
                    run_id,status,dataset_version_id,feature_set_id,model_fingerprint,
                    mlflow_run_id,manifest_path,created_at_utc
                ) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET
                    status=excluded.status,manifest_path=excluded.manifest_path""",
                (
                    run_id,
                    status,
                    version_id,
                    feature.get("featureStoreId"),
                    model.get("fingerprint"),
                    run_id,
                    str(path),
                    str(manifest.get("createdAtUtc") or datetime.now(timezone.utc).isoformat()),
                ),
            )
        return True

    def start_pipeline_run(
        self, run_id: str, run_kind: str, *, manifest_path: str | Path | None = None
    ) -> None:
        self.initialize()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO pipeline_runs(
                    run_id,run_kind,status,manifest_path,started_at_utc
                ) VALUES(?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET
                    status='RUNNING',manifest_path=excluded.manifest_path,
                    started_at_utc=excluded.started_at_utc,finished_at_utc=NULL,error_code=NULL""",
                (
                    run_id,
                    run_kind,
                    "RUNNING",
                    str(Path(manifest_path).resolve()) if manifest_path else None,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def register_legacy_record(
        self,
        *,
        record_id: str,
        record_kind: str,
        status: str,
        source_path: str | Path,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        self.initialize()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO legacy_records(
                    record_id,record_kind,status,source_path,metadata_json,created_at_utc
                ) VALUES(?,?,?,?,?,?) ON CONFLICT(record_id) DO UPDATE SET
                    status=excluded.status,source_path=excluded.source_path,
                    metadata_json=excluded.metadata_json""",
                (
                    record_id,
                    record_kind,
                    status,
                    str(Path(source_path).resolve()),
                    json.dumps(dict(metadata or {}), ensure_ascii=False, sort_keys=True),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def finish_pipeline_run(
        self,
        run_id: str,
        *,
        status: str,
        dataset_version_id: str | None = None,
        manifest_path: str | Path | None = None,
        error_code: str | None = None,
    ) -> None:
        if status not in {"SUCCEEDED", "FAILED", "PROMOTED", "REJECTED"}:
            raise ValueError(f"invalid terminal pipeline status: {status}")
        self.initialize()
        with self.connect() as connection:
            connection.execute(
                """UPDATE pipeline_runs SET status=?,dataset_version_id=?,manifest_path=COALESCE(?,manifest_path),
                    error_code=?,finished_at_utc=? WHERE run_id=?""",
                (
                    status,
                    dataset_version_id,
                    str(Path(manifest_path).resolve()) if manifest_path else None,
                    error_code,
                    datetime.now(timezone.utc).isoformat(),
                    run_id,
                ),
            )

    @staticmethod
    def _version(row: sqlite3.Row) -> DatasetVersion:
        return DatasetVersion(
            str(row["version_id"]),
            str(row["dataset_name"]),
            str(row["layer"]),
            str(row["status"]),
            Path(str(row["manifest_path"])),
            Path(str(row["data_path"])),
            str(row["created_at_utc"]),
        )
