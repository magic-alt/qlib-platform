from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _safe_component(value: str, name: str) -> str:
    normalized = value.strip()
    if not _SAFE_COMPONENT.fullmatch(normalized):
        raise ValueError(f"{name} contains unsafe characters")
    return normalized


@dataclass(frozen=True)
class ObjectRef:
    project_id: str
    digest: str
    size_bytes: int
    media_type: str = "application/octet-stream"

    def __post_init__(self) -> None:
        _safe_component(self.project_id, "project_id")
        if not _DIGEST.fullmatch(self.digest):
            raise ValueError("digest must be a lowercase SHA-256 hex string")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")
        if not self.media_type.strip():
            raise ValueError("media_type must be non-empty")


class ObjectStore(Protocol):
    def put(self, project_id: str, payload: bytes, *, media_type: str = "application/octet-stream") -> ObjectRef: ...

    def get(self, project_id: str, ref: ObjectRef) -> bytes: ...


class MemoryObjectStore:
    """Cloud-neutral reference backend used by contract tests and embedded callers."""

    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], bytes] = {}

    def put(self, project_id: str, payload: bytes, *, media_type: str = "application/octet-stream") -> ObjectRef:
        project = _safe_component(project_id, "project_id")
        digest = hashlib.sha256(payload).hexdigest()
        self._objects.setdefault((project, digest), bytes(payload))
        return ObjectRef(project, digest, len(payload), media_type)

    def get(self, project_id: str, ref: ObjectRef) -> bytes:
        project = _safe_component(project_id, "project_id")
        if ref.project_id != project:
            raise PermissionError("cross-project object access is forbidden")
        try:
            payload = self._objects[(project, ref.digest)]
        except KeyError as exc:
            raise KeyError(f"object {ref.digest!r} does not exist") from exc
        if hashlib.sha256(payload).hexdigest() != ref.digest:
            raise ValueError("object content digest mismatch")
        return bytes(payload)


class FileSystemObjectStore:
    """Content-addressed immutable filesystem backend for standalone/offline use."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, project_id: str, payload: bytes, *, media_type: str = "application/octet-stream") -> ObjectRef:
        project = _safe_component(project_id, "project_id")
        digest = hashlib.sha256(payload).hexdigest()
        path = self._object_path(project, digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.is_symlink():
                raise PermissionError("symlink object paths are forbidden")
            existing = path.read_bytes()
            if hashlib.sha256(existing).hexdigest() != digest:
                raise ValueError("existing immutable object is corrupt")
        else:
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(payload)
            temporary.replace(path)
        return ObjectRef(project, digest, len(payload), media_type)

    def get(self, project_id: str, ref: ObjectRef) -> bytes:
        project = _safe_component(project_id, "project_id")
        if ref.project_id != project:
            raise PermissionError("cross-project object access is forbidden")
        path = self._object_path(project, ref.digest)
        if path.is_symlink():
            raise PermissionError("symlink object paths are forbidden")
        if not path.is_file():
            raise KeyError(f"object {ref.digest!r} does not exist")
        payload = path.read_bytes()
        if len(payload) != ref.size_bytes or hashlib.sha256(payload).hexdigest() != ref.digest:
            raise ValueError("object content does not match immutable reference")
        return payload

    def _object_path(self, project_id: str, digest: str) -> Path:
        if not _DIGEST.fullmatch(digest):
            raise ValueError("digest must be a lowercase SHA-256 hex string")
        project_root = (self.root / project_id).resolve()
        candidate = project_root / "objects" / "sha256" / digest[:2] / digest
        resolved_parent = candidate.parent.resolve()
        if not resolved_parent.is_relative_to(project_root):
            raise PermissionError("object path escapes project namespace")
        return candidate


class ProjectWorkspace:
    """Safe project-local mutable workspace resolver; no cross-project relative traversal."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, project_id: str, relative_path: str | Path) -> Path:
        project = _safe_component(project_id, "project_id")
        relative = Path(relative_path)
        if relative.is_absolute():
            raise PermissionError("absolute workspace paths are forbidden")
        project_root = (self.root / project).resolve()
        project_root.mkdir(parents=True, exist_ok=True)
        candidate = (project_root / relative).resolve()
        if not candidate.is_relative_to(project_root):
            raise PermissionError("workspace path escapes project namespace")
        return candidate


@dataclass(frozen=True)
class ArtifactMetadata:
    project_id: str
    artifact_id: str
    object_ref: ObjectRef
    manifest_digest: str
    entitlement_id: str | None = None
    ref_count: int = 1
    retention_until_utc: str | None = None

    def __post_init__(self) -> None:
        _safe_component(self.project_id, "project_id")
        _safe_component(self.artifact_id, "artifact_id")
        if self.object_ref.project_id != self.project_id:
            raise ValueError("artifact metadata and object ref must share project_id")
        if not _DIGEST.fullmatch(self.manifest_digest):
            raise ValueError("manifest_digest must be a lowercase SHA-256 hex string")
        if self.ref_count < 0:
            raise ValueError("ref_count must be non-negative")


class MetadataStore(Protocol):
    def register(self, metadata: ArtifactMetadata) -> ArtifactMetadata: ...

    def get(self, project_id: str, artifact_id: str) -> ArtifactMetadata | None: ...

    def add_reference(self, project_id: str, artifact_id: str) -> ArtifactMetadata: ...

    def release_reference(self, project_id: str, artifact_id: str) -> ArtifactMetadata: ...


class MemoryMetadataStore:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], ArtifactMetadata] = {}

    def register(self, metadata: ArtifactMetadata) -> ArtifactMetadata:
        key = (metadata.project_id, metadata.artifact_id)
        existing = self._records.get(key)
        if existing is not None and existing != metadata:
            raise ValueError("immutable artifact metadata already exists with different content")
        self._records.setdefault(key, metadata)
        return self._records[key]

    def get(self, project_id: str, artifact_id: str) -> ArtifactMetadata | None:
        return self._records.get((_safe_component(project_id, "project_id"), artifact_id))

    def add_reference(self, project_id: str, artifact_id: str) -> ArtifactMetadata:
        record = self._required(project_id, artifact_id)
        updated = replace(record, ref_count=record.ref_count + 1)
        self._records[(record.project_id, record.artifact_id)] = updated
        return updated

    def release_reference(self, project_id: str, artifact_id: str) -> ArtifactMetadata:
        record = self._required(project_id, artifact_id)
        updated = replace(record, ref_count=max(0, record.ref_count - 1))
        self._records[(record.project_id, record.artifact_id)] = updated
        return updated

    def _required(self, project_id: str, artifact_id: str) -> ArtifactMetadata:
        record = self.get(project_id, artifact_id)
        if record is None:
            raise KeyError(f"unknown artifact {artifact_id!r}")
        return record


class SqliteMetadataStore:
    """Local metadata backend. Payload bytes remain exclusively in the object store."""

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database).expanduser().resolve()

    def initialize(self) -> None:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS artifact_metadata (
                    project_id TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    object_digest TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    media_type TEXT NOT NULL,
                    manifest_digest TEXT NOT NULL,
                    entitlement_id TEXT,
                    ref_count INTEGER NOT NULL,
                    retention_until_utc TEXT,
                    PRIMARY KEY(project_id, artifact_id)
                )"""
            )

    def register(self, metadata: ArtifactMetadata) -> ArtifactMetadata:
        self.initialize()
        existing = self.get(metadata.project_id, metadata.artifact_id)
        if existing is not None:
            if existing != metadata:
                raise ValueError("immutable artifact metadata already exists with different content")
            return existing
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO artifact_metadata VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    metadata.project_id,
                    metadata.artifact_id,
                    metadata.object_ref.digest,
                    metadata.object_ref.size_bytes,
                    metadata.object_ref.media_type,
                    metadata.manifest_digest,
                    metadata.entitlement_id,
                    metadata.ref_count,
                    metadata.retention_until_utc,
                ),
            )
        return metadata

    def get(self, project_id: str, artifact_id: str) -> ArtifactMetadata | None:
        self.initialize()
        project = _safe_component(project_id, "project_id")
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                """SELECT object_digest,size_bytes,media_type,manifest_digest,entitlement_id,
                ref_count,retention_until_utc FROM artifact_metadata
                WHERE project_id=? AND artifact_id=?""",
                (project, artifact_id),
            ).fetchone()
        if row is None:
            return None
        ref = ObjectRef(project, str(row[0]), int(row[1]), str(row[2]))
        return ArtifactMetadata(project, artifact_id, ref, str(row[3]), row[4], int(row[5]), row[6])

    def add_reference(self, project_id: str, artifact_id: str) -> ArtifactMetadata:
        return self._change_reference(project_id, artifact_id, 1)

    def release_reference(self, project_id: str, artifact_id: str) -> ArtifactMetadata:
        return self._change_reference(project_id, artifact_id, -1)

    def _change_reference(self, project_id: str, artifact_id: str, delta: int) -> ArtifactMetadata:
        record = self.get(project_id, artifact_id)
        if record is None:
            raise KeyError(f"unknown artifact {artifact_id!r}")
        target = max(0, record.ref_count + delta)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE artifact_metadata SET ref_count=? WHERE project_id=? AND artifact_id=?",
                (target, record.project_id, artifact_id),
            )
        updated = self.get(record.project_id, artifact_id)
        if updated is None:  # pragma: no cover - defensive consistency check
            raise RuntimeError("metadata disappeared during reference update")
        return updated
