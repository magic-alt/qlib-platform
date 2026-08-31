from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, cast

from .lineage import sha256_json
from .store import sha256_file
from .verification import (
    deterministic_sample,
    load_verification_receipt,
    normalize_verification_mode,
    write_verification_receipt,
)


DATASET_MANIFEST_SCHEMA = "3.0"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def _integer(value: object) -> int:
    return int(value) if isinstance(value, (str, int, float)) else 0


def collect_partitions(
    root: Path, *, exclude: Iterable[str] = ("dataset_manifest.json",)
) -> list[dict[str, object]]:
    excluded = set(exclude)
    partitions: list[dict[str, object]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file() and item.name not in excluded):
        relative = path.relative_to(root).as_posix()
        partitions.append(
            {
                "partition_key": relative,
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return partitions


def content_version_id(
    *,
    dataset_name: str,
    layer: str,
    partitions: Iterable[Mapping[str, object]],
    semantic_contract: Mapping[str, object],
    parents: Iterable[Mapping[str, object]] = (),
) -> str:
    payload = {
        "dataset_name": dataset_name,
        "layer": layer,
        "partitions": [
            {
                "path": str(item.get("path", "")),
                "bytes": _integer(item.get("bytes", 0)),
                "sha256": str(item.get("sha256", "")),
            }
            for item in sorted(partitions, key=lambda value: str(value.get("path", "")))
        ],
        "semantic_contract": dict(semantic_contract),
        "parents": sorted(
            [dict(parent) for parent in parents], key=lambda value: str(value.get("version_id", ""))
        ),
    }
    return sha256_json(payload)


def write_dataset_manifest(
    root: Path,
    *,
    dataset_name: str,
    layer: str,
    semantic_contract: Mapping[str, object],
    parents: Iterable[Mapping[str, object]] = (),
    coverage: Mapping[str, object] | None = None,
    quality: Mapping[str, object] | None = None,
    extra: Mapping[str, object] | None = None,
    final_data_path: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    partitions = collect_partitions(root)
    parent_list = [dict(parent) for parent in parents]
    version_id = content_version_id(
        dataset_name=dataset_name,
        layer=layer,
        partitions=partitions,
        semantic_contract=semantic_contract,
        parents=parent_list,
    )
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "schema_version": DATASET_MANIFEST_SCHEMA,
        "dataset_name": dataset_name,
        "layer": layer,
        "version_id": version_id,
        "build_id": f"{now:%Y%m%dT%H%M%SZ}-{version_id[:12]}-{uuid.uuid4().hex[:12]}",
        "status": "VALIDATED",
        "created_at_utc": now.isoformat(),
        "data_path": str((final_data_path or root).resolve()),
        "coverage": dict(coverage or {}),
        "semantic_contract": dict(semantic_contract),
        "parents": parent_list,
        "partitions": partitions,
        "quality": dict(quality or {}),
    }
    if extra:
        payload.update(extra)
    path = root / "dataset_manifest.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return path, payload


def _partition_value(raw: object) -> str:
    value = str(raw or "")
    parts = value.split("/")
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or (len(value) > 1 and value[1] == ":")
        or ".." in parts
    ):
        raise ValueError(f"invalid dataset partition path: {value!r}")
    return value


def _partition_path(data_path: Path, raw: object, *, resolve_containment: bool = False) -> Path:
    value = _partition_value(raw)
    target = data_path / value
    if resolve_containment:
        try:
            target.resolve().relative_to(data_path.resolve())
        except ValueError as exc:
            raise ValueError(f"dataset partition escapes data path: {value!r}") from exc
    return target


def verify_dataset_manifest(
    path: str | Path,
    *,
    mode: str = "deep",
    verify_files: bool | None = None,
    receipt_dir: str | Path | None = None,
    reuse_receipt: bool = False,
    sample_size: int = 64,
    evidence: dict[str, object] | None = None,
    cas_root: str | Path | None = None,
    verified_digests: set[str] | None = None,
    workers: int = 1,
) -> dict[str, object]:
    if workers < 1:
        raise ValueError("verification workers must be positive")
    if verify_files is not None:
        compatibility_mode = "deep" if verify_files else "manifest"
        if mode != "deep" and mode != compatibility_mode:
            raise ValueError("verify_files cannot conflict with verification mode")
        mode = compatibility_mode
    normalized_mode = normalize_verification_mode(mode)
    manifest_path = Path(path)
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    payload = json.loads(manifest_bytes)
    if payload.get("schema_version") != DATASET_MANIFEST_SCHEMA:
        raise ValueError(f"unsupported dataset manifest schema: {payload.get('schema_version')}")
    data_path = Path(str(payload["data_path"]))
    if data_path.is_symlink() or not data_path.is_dir():
        raise FileNotFoundError(f"dataset data path is missing: {data_path}")
    raw_partitions = payload.get("partitions", [])
    if not isinstance(raw_partitions, list):
        raise ValueError("dataset partitions must be a list")
    partitions: list[Mapping[str, object]] = []
    seen: set[str] = set()
    total_bytes = 0
    for raw_partition in raw_partitions:
        if not isinstance(raw_partition, dict):
            raise ValueError("dataset partition must be an object")
        partition = cast(Mapping[str, object], raw_partition)
        partition_path = str(partition.get("path") or "")
        _partition_value(partition_path)
        if partition_path in seen:
            raise ValueError(f"duplicate dataset partition path: {partition_path}")
        seen.add(partition_path)
        expected_digest = str(partition.get("sha256") or "").lower()
        if _SHA256_PATTERN.fullmatch(expected_digest) is None:
            raise ValueError(f"invalid dataset partition checksum: {partition_path}")
        expected_bytes = _integer(partition.get("bytes"))
        if expected_bytes < 0:
            raise ValueError(f"invalid dataset partition size: {partition_path}")
        total_bytes += expected_bytes
        partitions.append(partition)
    expected = content_version_id(
        dataset_name=str(payload["dataset_name"]),
        layer=str(payload["layer"]),
        partitions=payload.get("partitions", []),
        semantic_contract=payload.get("semantic_contract", {}),
        parents=payload.get("parents", []),
    )
    if expected != payload.get("version_id"):
        raise ValueError("dataset version_id does not match its content contract")
    receipt = None
    if normalized_mode == "deep" and reuse_receipt and receipt_dir is not None:
        receipt = load_verification_receipt(
            receipt_dir,
            artifact_kind="dataset",
            artifact_id=str(payload["version_id"]),
            manifest_sha256=manifest_sha256,
        )
        if receipt is not None:
            _, receipt_payload = receipt
            if (
                _integer(receipt_payload.get("fileCount", -1)) != len(partitions)
                or _integer(receipt_payload.get("totalBytes", -1)) != total_bytes
            ):
                raise ValueError("verification receipt file inventory mismatch")
    selected: list[Mapping[str, object]] = []
    if normalized_mode == "sampled":
        selected = deterministic_sample(
            partitions,
            identity=str(payload["version_id"]),
            path_key="path",
            sample_size=sample_size,
        )
    elif normalized_mode == "deep":
        selected = partitions
    resolved_cas = Path(cas_root).expanduser().resolve() if cas_root is not None else None

    def verify_partition(partition: Mapping[str, object]) -> bool:
        file_path = _partition_path(data_path, partition["path"], resolve_containment=True)
        if file_path.is_symlink() or not file_path.is_file():
            raise ValueError(f"dataset partition checksum mismatch: {file_path}")
        if file_path.stat().st_size != _integer(partition.get("bytes")):
            raise ValueError(f"dataset partition checksum mismatch (size drift): {file_path}")
        expected_digest = str(partition.get("sha256") or "")
        cas_object = (
            resolved_cas / expected_digest[:2] / expected_digest if resolved_cas is not None else None
        )
        trusted_cas_link = (
            cas_object is not None
            and verified_digests is not None
            and expected_digest in verified_digests
            and cas_object.is_file()
            and os.path.samefile(file_path, cas_object)
        )
        if trusted_cas_link:
            return True
        elif sha256_file(file_path) != expected_digest:
            raise ValueError(f"dataset partition checksum mismatch: {file_path}")
        return False

    if workers == 1 or len(selected) < 2:
        cas_results = [verify_partition(partition) for partition in selected]
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dataset-verify") as executor:
            cas_results = list(executor.map(verify_partition, selected))
    verified_via_cas = sum(cas_results)
    receipt_path: Path | None = receipt[0] if receipt is not None else None
    if normalized_mode == "deep" and receipt is None and receipt_dir is not None:
        receipt_path = write_verification_receipt(
            receipt_dir,
            artifact_kind="dataset",
            artifact_id=str(payload["version_id"]),
            manifest_sha256=manifest_sha256,
            file_count=len(partitions),
            total_bytes=total_bytes,
        )
    if evidence is not None:
        evidence.update(
            {
                "mode": normalized_mode,
                "verificationSource": "receipt+files" if receipt is not None else "files",
                "manifestSha256": manifest_sha256,
                "fileCount": len(partitions),
                "verifiedFileCount": len(selected),
                "verifiedViaCasCount": verified_via_cas,
                "workers": workers,
                "totalBytes": total_bytes,
                "receipt": str(receipt_path) if receipt_path is not None else None,
            }
        )
    return cast(dict[str, object], payload)
