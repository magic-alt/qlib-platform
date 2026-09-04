from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import uuid
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


def _json_object(raw: bytes | str, *, name: str) -> dict[str, Any]:
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        raise ValueError(f"{name} response must be a JSON object")
    return dict(loaded)


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    token = os.getenv("LEAN_API_TOKEN", "").strip()
    token_file = os.getenv("LEAN_API_TOKEN_FILE", "").strip()
    if not token and token_file:
        token = Path(token_file).expanduser().read_text(encoding="utf-8").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _upload(base_url: str, object_key: str, path: Path) -> dict[str, Any]:
    boundary = f"----qlib-{uuid.uuid4().hex}"
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode()
        + path.read_bytes()
        + f"\r\n--{boundary}--\r\n".encode()
    )
    headers = {**_headers(), "Content-Type": f"multipart/form-data; boundary={boundary}"}
    request = Request(
        f"{base_url.rstrip('/')}/api/object-store/{object_key}", data=body, headers=headers, method="POST"
    )
    with urlopen(request, timeout=120) as response:
        return _json_object(response.read(), name="object-store upload")


def register_manifest(manifest_path: str | Path, *, base_url: str | None = None) -> dict[str, Any]:
    path = Path(manifest_path).expanduser().resolve()
    payload = _json_object(path.read_text(encoding="utf-8"), name="manifest")
    url = (base_url or os.getenv("LEAN_API_URL") or "http://127.0.0.1:8000").rstrip("/")
    external_run_id = str(payload.get("externalRunId") or payload.get("external_run_id") or "").strip()
    if not external_run_id:
        raise ValueError("manifest externalRunId is required")
    artifacts = payload.get("artifacts") or []
    registered: list[dict[str, Any]] = []
    for artifact in artifacts:
        local = artifact.get("localPath")
        if not local:
            registered.append(dict(artifact))
            continue
        file_path = Path(local).expanduser().resolve()
        object_key = str(artifact.get("objectKey") or f"qlib/{external_run_id}/{file_path.name}")
        digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
        _upload(url, object_key, file_path)
        registered.append({**artifact, "objectKey": object_key, "sha256": digest, "localPath": None})
    payload["artifacts"] = registered
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    headers = {**_headers(), "Content-Type": "application/json", "Idempotency-Key": external_run_id}
    request = Request(f"{url}/api/research/imports/qlib", data=body, headers=headers, method="POST")
    with urlopen(request, timeout=60) as response:
        return _json_object(response.read(), name="manifest registration")
