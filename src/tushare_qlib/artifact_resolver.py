from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Mapping
from urllib.parse import urlparse


class ArtifactResolutionError(ValueError):
    pass


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ArtifactResolver:
    """Resolve portable artifact URIs into one configured local artifact root."""

    _KINDS = {"research", "deployment", "signal"}

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        roots: Mapping[str, str | Path] | None = None,
    ) -> None:
        if root is None and not roots:
            raise ValueError("artifact resolver requires a root or per-kind roots")
        base = Path(root).expanduser().resolve() if root is not None else None
        self.roots = {kind: Path(value).expanduser().resolve() for kind, value in (roots or {}).items()}
        if base is not None:
            for kind in self._KINDS:
                self.roots.setdefault(kind, base / kind)

    def resolve(self, uri: str, *, expected_sha256: str | None = None) -> Path:
        parsed = urlparse(uri)
        if parsed.scheme != "artifact" or parsed.netloc not in self._KINDS:
            raise ArtifactResolutionError(f"unsupported artifact URI: {uri}")
        relative = PurePosixPath(parsed.path.lstrip("/"))
        if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            raise ArtifactResolutionError(f"unsafe artifact URI: {uri}")
        kind_root = self.roots.get(parsed.netloc)
        if kind_root is None:
            raise ArtifactResolutionError(f"artifact kind is not configured: {parsed.netloc}")
        target = (kind_root / Path(*relative.parts)).resolve()
        try:
            target.relative_to(kind_root)
        except ValueError as exc:
            raise ArtifactResolutionError(f"artifact URI escapes configured root: {uri}") from exc
        if not target.is_file():
            raise ArtifactResolutionError(f"artifact is missing: {uri}")
        if expected_sha256 and sha256_path(target) != expected_sha256:
            raise ArtifactResolutionError(f"artifact checksum mismatch: {uri}")
        return target

    @staticmethod
    def deployment_uri(deployment_id: str, name: str = "model_manifest.json") -> str:
        return f"artifact://deployment/{deployment_id}/{name}"

    @staticmethod
    def signal_uri(signal_id: str, name: str = "manifest.json") -> str:
        return f"artifact://signal/{signal_id}/{name}"

    @staticmethod
    def research_uri(run_id: str, name: str = "manifest.json") -> str:
        return f"artifact://research/{run_id}/{name}"
