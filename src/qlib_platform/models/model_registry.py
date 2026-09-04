from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from qlib_platform.artifacts.artifact_resolver import sha256_path
from qlib_platform.models.model_bundle import bundle_uri, load_model_bundle, verify_model_bundle
from qlib_platform.ops.ops_state import OpsState
from qlib_platform.settings import Settings


class ModelRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state = OpsState(settings.paths.state / "ops.sqlite3")

    def register_bundle(self, manifest_path: str | Path) -> dict[str, Any]:
        path = Path(manifest_path)
        manifest = verify_model_bundle(path.parent)
        digest = sha256_path(path.parent / "checksums.json")
        self.state.register_deployment(manifest, bundle_uri(manifest), digest)
        return self.state.deployment(str(manifest["deploymentId"]))

    def deploy(self, deployment_id: str, *, device: str = "cpu") -> dict[str, Any]:
        record = self.state.deployment(deployment_id)
        manifest = json.loads(record["metadata_json"])
        root = self.settings.paths.models / "deployments" / deployment_id
        loaded = load_model_bundle(root, device=device, verify_parity=True)
        if loaded.manifest != manifest:
            raise ValueError("registry metadata does not match immutable bundle manifest")
        self.state.deploy(deployment_id)
        return self.state.current_deployment()

    def rollback(self, deployment_id: str, *, device: str = "cpu") -> dict[str, Any]:
        record = self.state.deployment(deployment_id)
        if record["status"] != "RETIRED":
            raise ValueError("rollback target must be RETIRED")
        return self.deploy(deployment_id, device=device)

    def current(self) -> dict[str, Any]:
        return self.state.current_deployment()

    def bundle_root(self, deployment_id: str) -> Path:
        record = self.state.deployment(deployment_id)
        root = self.settings.paths.models / "deployments" / deployment_id
        digest = sha256_path(root / "checksums.json")
        if digest != record["bundle_sha256"]:
            raise ValueError("registry bundle checksum mismatch")
        return root
