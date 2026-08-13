from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, cast

from .lineage import sha256_json
from .store import sha256_file


DATASET_MANIFEST_SCHEMA = "3.0"


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
        "build_id": f"{now:%Y%m%dT%H%M%SZ}-{version_id[:12]}",
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


def verify_dataset_manifest(path: str | Path, *, verify_files: bool = True) -> dict[str, object]:
    manifest_path = Path(path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != DATASET_MANIFEST_SCHEMA:
        raise ValueError(f"unsupported dataset manifest schema: {payload.get('schema_version')}")
    data_path = Path(str(payload["data_path"]))
    if not data_path.is_dir():
        raise FileNotFoundError(f"dataset data path is missing: {data_path}")
    if verify_files:
        for partition in payload.get("partitions", []):
            file_path = data_path / str(partition["path"])
            if not file_path.is_file() or sha256_file(file_path) != partition.get("sha256"):
                raise ValueError(f"dataset partition checksum mismatch: {file_path}")
    expected = content_version_id(
        dataset_name=str(payload["dataset_name"]),
        layer=str(payload["layer"]),
        partitions=payload.get("partitions", []),
        semantic_contract=payload.get("semantic_contract", {}),
        parents=payload.get("parents", []),
    )
    if expected != payload.get("version_id"):
        raise ValueError("dataset version_id does not match its content contract")
    return cast(dict[str, object], payload)
