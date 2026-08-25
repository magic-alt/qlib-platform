from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping


VERIFICATION_MODES = frozenset({"manifest", "sampled", "deep"})


def _receipt_path(root: str | Path, artifact_kind: str, artifact_id: str, manifest_sha256: str) -> Path:
    artifact_key = hashlib.sha256(artifact_id.encode()).hexdigest()[:24]
    return Path(root).expanduser().resolve() / artifact_kind / artifact_key / f"{manifest_sha256[:24]}.json"


def normalize_verification_mode(mode: str) -> str:
    normalized = str(mode).strip().lower()
    if normalized not in VERIFICATION_MODES:
        raise ValueError(f"unsupported verification mode: {mode}")
    return normalized


def deterministic_sample(
    entries: Iterable[Mapping[str, object]],
    *,
    identity: str,
    path_key: str,
    sample_size: int = 64,
) -> list[Mapping[str, object]]:
    ordered = sorted(entries, key=lambda item: str(item.get(path_key) or ""))
    if not ordered:
        return []
    selected: dict[str, Mapping[str, object]] = {
        str(ordered[0].get(path_key) or ""): ordered[0],
        str(ordered[-1].get(path_key) or ""): ordered[-1],
    }
    directories: dict[str, Mapping[str, object]] = {}
    for item in ordered:
        path = str(item.get(path_key) or "")
        directories.setdefault(Path(path).parent.as_posix(), item)
    for item in directories.values():
        selected[str(item.get(path_key) or "")] = item
    ranked = sorted(
        ordered,
        key=lambda item: hashlib.sha256(f"{identity}\0{str(item.get(path_key) or '')}".encode()).digest(),
    )
    target = max(sample_size, len(selected))
    for item in ranked:
        path = str(item.get(path_key) or "")
        selected[path] = item
        if len(selected) >= min(target, len(ordered)):
            break
    return [selected[path] for path in sorted(selected)]


def write_verification_receipt(
    root: str | Path,
    *,
    artifact_kind: str,
    artifact_id: str,
    manifest_sha256: str,
    file_count: int,
    total_bytes: int,
) -> Path:
    target = _receipt_path(root, artifact_kind, artifact_id, manifest_sha256)
    receipt_root = target.parent
    receipt_root.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "schemaVersion": "1.0",
        "artifactKind": artifact_kind,
        "artifactId": artifact_id,
        "manifestSha256": manifest_sha256,
        "mode": "deep",
        "verifiedAt": datetime.now(timezone.utc).isoformat(),
        "fileCount": int(file_count),
        "totalBytes": int(total_bytes),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    payload["receiptSha256"] = hashlib.sha256(canonical).hexdigest()
    descriptor, temporary_name = tempfile.mkstemp(prefix=".receipt.", dir=receipt_root)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
        )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def load_verification_receipt(
    root: str | Path,
    *,
    artifact_kind: str,
    artifact_id: str,
    manifest_sha256: str,
) -> tuple[Path, dict[str, object]] | None:
    path = _receipt_path(root, artifact_kind, artifact_id, manifest_sha256)
    if not path.is_file() or path.is_symlink():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("verification receipt must be a JSON object")
    checksum = str(payload.pop("receiptSha256", ""))
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(canonical).hexdigest() != checksum:
        raise ValueError("verification receipt checksum mismatch")
    payload["receiptSha256"] = checksum
    expected = {
        "schemaVersion": "1.0",
        "artifactKind": artifact_kind,
        "artifactId": artifact_id,
        "manifestSha256": manifest_sha256,
        "mode": "deep",
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise ValueError("verification receipt identity mismatch")
    return path, payload
