from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urlparse

from qlib_platform.data.store import sha256_file
from qlib_platform.lineage import git_revision
from qlib_platform.settings import Settings

RUN_MANIFEST_SCHEMA = "qlib-platform.run-manifest.v1"
RUN_MANIFEST_FILE = "run_manifest.json"
RUN_MANIFEST_STORE = "run_manifests"

_SECRET_MARKERS = ("token", "secret", "password", "credential", "api_key", "apikey")
_NON_SEMANTIC_KEYS = {
    "title", "display_title", "displayName", "description", "notes", "comment", "comments",
    "dashboard", "dashboardError", "log_level", "logLevel", "verbose", "verbose_child_output",
    "output", "output_dir", "localPath", "path", "createdAtUtc", "created_at_utc",
    "startedAtUtc", "finishedAtUtc", "updatedAtUtc", "updated_at_utc",
}
_VOLATILE_MANIFEST_KEYS = {"created_at_utc", "manifest_digest", "local_path", "uri"}
_BUSINESS_OUTPUT_ROLES = {"model", "prediction", "selection", "portfolio", "backtest"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SECRET_MARKERS)


def redact_secrets(value: Any, path: str = "config") -> tuple[Any, list[str]]:
    refs: list[str] = []
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            current = f"{path}.{key}"
            if _sensitive(key) and item is not None and item != "":
                reference = f"config:{current}"
                out[key] = {"secretReference": reference}
                refs.append(reference)
            else:
                out[key], child_refs = redact_secrets(item, current)
                refs.extend(child_refs)
        return out, refs
    if isinstance(value, list):
        out_list = []
        for index, item in enumerate(value):
            clean, child_refs = redact_secrets(item, f"{path}[{index}]")
            out_list.append(clean)
            refs.extend(child_refs)
        return out_list, refs
    return value, refs


def canonical_business_value(value: Any) -> Any:
    """Project metadata onto business/scientific identity, excluding presentation-only state."""
    if isinstance(value, Mapping):
        return {
            str(key): canonical_business_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in _NON_SEMANTIC_KEYS and not _sensitive(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [canonical_business_value(item) for item in value]
    if isinstance(value, Path):
        return value.name
    return value


def canonical_business_sha256(value: Any) -> str:
    return _sha256_json(canonical_business_value(value))


def _package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in ("qlib-platform", "pyqlib", "lightgbm", "numpy", "pandas", "pyarrow"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def environment_identity() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "packages": _package_versions(),
    }
    payload["fingerprint"] = canonical_business_sha256(payload)
    return payload


def config_identity(settings: Settings) -> dict[str, Any]:
    clean, refs = redact_secrets(settings.data)
    semantic = canonical_business_value(clean)
    return {
        "canonical_sha256": _sha256_json(semantic),
        "secret_references": sorted(set(refs)),
        "source_sha256": sha256_file(settings.config_path) if settings.config_path.is_file() else None,
    }


def project_git_identity() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[3]
    payload = dict(git_revision(root))
    payload["dirty_policy"] = "clean-required-for-deterministic-replay"
    return payload


def _artifact_role(path: Path, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    name = path.name.lower()
    if "model" in name or name.endswith((".pkl", ".pt", ".pth", ".onnx")):
        return "model"
    if "pred" in name or "signal" in name:
        return "prediction"
    if "selection" in name:
        return "selection"
    if "portfolio" in name or "position" in name:
        return "portfolio"
    if "backtest" in name or "report" in name or "metric" in name:
        return "backtest"
    if "dataset_manifest" in name:
        return "dataset"
    return "evidence"


def artifact_record(
    path: str | Path,
    *,
    role: str | None = None,
    required: bool = True,
    semantic_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    metadata = canonical_business_value(dict(semantic_metadata or {}))
    return {
        "name": resolved.name,
        "role": _artifact_role(resolved, role),
        "required": bool(required),
        "uri": resolved.as_uri(),
        "local_path": str(resolved),
        "content_sha256": sha256_file(resolved) if resolved.is_file() else "MISSING",
        "semantic_sha256": _sha256_json(metadata),
        "semantic_metadata": metadata,
    }


def _artifact_identity(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": item.get("name"),
        "role": item.get("role"),
        "required": bool(item.get("required", True)),
        "content_sha256": item.get("content_sha256"),
        "semantic_sha256": item.get("semantic_sha256"),
    }


def _dedupe_artifacts(artifacts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for item in artifacts:
        row = dict(item)
        unique[(str(row.get("role")), str(row.get("content_sha256")))] = row
    return sorted(unique.values(), key=lambda row: (str(row.get("role")), str(row.get("name"))))


def derive_stage_identities(components: Mapping[str, Any]) -> dict[str, str]:
    dataset = canonical_business_sha256(
        {"data": components.get("data", {}), "universe": components.get("universe", {})}
    )
    feature = canonical_business_sha256({"dataset": dataset, "feature": components.get("feature", {})})
    model = canonical_business_sha256(
        {
            "feature": feature,
            "label": components.get("label", {}),
            "split": components.get("split", {}),
            "model": components.get("model", {}),
            "code": components.get("code", {}),
            "environment": components.get("environment", {}),
        }
    )
    prediction = canonical_business_sha256({"model": model, "prediction": components.get("prediction", {})})
    backtest = canonical_business_sha256(
        {
            "prediction": prediction,
            "portfolio": components.get("portfolio", {}),
            "market": components.get("market", {}),
            "benchmark": components.get("benchmark", {}),
        }
    )
    return {
        "dataset_identity": f"dataset-{dataset[:24]}",
        "feature_identity": f"feature-{feature[:24]}",
        "model_identity": f"model-{model[:24]}",
        "prediction_identity": f"prediction-{prediction[:24]}",
        "backtest_identity": f"backtest-{backtest[:24]}",
    }


def _manifest_digest_payload(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): (
            [_artifact_identity(item) for item in value if isinstance(item, Mapping)]
            if key == "artifacts" and isinstance(value, list)
            else canonical_business_value(value)
        )
        for key, value in manifest.items()
        if str(key) not in _VOLATILE_MANIFEST_KEYS
    }


def build_run_manifest(
    *,
    source_kind: str,
    status: str,
    components: Mapping[str, Any],
    artifacts: Sequence[Mapping[str, Any]] = (),
    gates: Mapping[str, Any] | None = None,
    warnings: Sequence[str] = (),
    known_deviations: Sequence[str] = (),
    parent_run_id: str | None = None,
    reproduction: Mapping[str, Any] | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    normalized_artifacts = _dedupe_artifacts(artifacts)
    stage_ids = derive_stage_identities(components)
    definition = {
        "run_schema_version": RUN_MANIFEST_SCHEMA,
        "source_kind": source_kind,
        **{key: components.get(key, {}) for key in (
            "code", "resolved_config", "environment", "data", "universe", "feature", "label",
            "split", "model", "prediction", "portfolio", "market", "benchmark"
        )},
        "stage_identities": stage_ids,
    }
    definition_id = f"rundef-{canonical_business_sha256(definition)[:32]}"
    business_outputs = [
        _artifact_identity(item)
        for item in normalized_artifacts
        if item.get("role") in _BUSINESS_OUTPUT_ROLES
    ]
    run_id = "run-" + canonical_business_sha256(
        {"run_definition_id": definition_id, "business_outputs": business_outputs}
    )[:32]
    manifest: dict[str, Any] = {
        "run_schema_version": RUN_MANIFEST_SCHEMA,
        "run_id": run_id,
        "run_definition_id": definition_id,
        "parent_run_id": parent_run_id,
        "source_kind": source_kind,
        "status": status,
        "created_at_utc": created_at_utc or _utc_now(),
        **{key: components.get(key, {}) for key in (
            "code", "resolved_config", "environment", "data", "universe", "feature", "label",
            "split", "model", "prediction", "portfolio", "market", "benchmark"
        )},
        "stage_identities": stage_ids,
        "artifacts": normalized_artifacts,
        "gates": canonical_business_value(dict(gates or {})),
        "warnings": list(dict.fromkeys(str(item) for item in warnings)),
        "known_deviations": list(dict.fromkeys(str(item) for item in known_deviations)),
        "reproduction": canonical_business_value(dict(reproduction or {})),
    }
    manifest["manifest_digest"] = _sha256_json(_manifest_digest_payload(manifest))
    return manifest


def store_root(settings: Settings) -> Path:
    return settings.paths.state / RUN_MANIFEST_STORE


def write_run_manifest(manifest: Mapping[str, Any], *, local_root: Path, store: Path) -> Path:
    payload = dict(manifest)
    if payload.get("manifest_digest") != _sha256_json(_manifest_digest_payload(payload)):
        raise ValueError("run manifest digest mismatch before archive")
    run_id = str(payload.get("run_id") or "")
    if not run_id.startswith("run-"):
        raise ValueError("run manifest has invalid run_id")

    local_root.mkdir(parents=True, exist_ok=True)
    local = local_root / RUN_MANIFEST_FILE
    temporary = local.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, local)

    archive = store / run_id / "manifest.json"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.is_file():
        existing = json.loads(archive.read_text(encoding="utf-8"))
        if not isinstance(existing, Mapping) or existing.get("manifest_digest") != payload.get("manifest_digest"):
            raise ValueError(f"immutable run manifest collision: {run_id}")
        return archive
    temporary = archive.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, archive)
    return archive


def resolve_run_manifest(store: Path, run_id: str) -> Path:
    reference = str(run_id).strip()
    if not reference.startswith("run-") or "/" in reference or "\\" in reference:
        raise ValueError("run reference must be an immutable run_id; aliases such as latest are forbidden")
    path = store / reference / "manifest.json"
    if not path.is_file():
        raise FileNotFoundError(f"run manifest not found for immutable id: {reference}")
    return path


def _artifact_path(item: Mapping[str, Any]) -> Path:
    raw = str(item.get("local_path") or "")
    if raw:
        return Path(raw).expanduser().resolve()
    parsed = urlparse(str(item.get("uri") or ""))
    if parsed.scheme != "file":
        return Path("__unsupported_non_file_artifact__")
    return Path(unquote(parsed.path)).resolve()


def verify_run_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).expanduser().resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("run manifest root must be an object")
    if payload.get("run_schema_version") != RUN_MANIFEST_SCHEMA:
        raise ValueError(f"unsupported run manifest schema: {payload.get('run_schema_version')}")
    if payload.get("manifest_digest") != _sha256_json(_manifest_digest_payload(payload)):
        raise ValueError("run manifest digest mismatch")

    errors: list[dict[str, str]] = []
    verified: list[str] = []
    for item in payload.get("artifacts", []):
        if not isinstance(item, Mapping):
            errors.append({"location": "artifacts", "error": "artifact entry is not an object"})
            continue
        artifact = _artifact_path(item)
        if not artifact.is_file():
            if bool(item.get("required", True)):
                errors.append({"location": str(artifact), "error": "required artifact is missing"})
            continue
        if sha256_file(artifact) != item.get("content_sha256"):
            errors.append({"location": str(artifact), "error": "artifact content checksum mismatch"})
            continue
        metadata = canonical_business_value(item.get("semantic_metadata", {}))
        if _sha256_json(metadata) != item.get("semantic_sha256"):
            errors.append({"location": str(artifact), "error": "artifact semantic metadata checksum mismatch"})
            continue
        verified.append(str(artifact))
    return {
        "run_id": payload.get("run_id"),
        "run_definition_id": payload.get("run_definition_id"),
        "passed": not errors,
        "verified_artifacts": verified,
        "errors": errors,
        "manifest": str(manifest_path),
    }


def verify_runtime_context(settings: Settings, manifest: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    expected_config = manifest.get("resolved_config", {})
    expected_config = expected_config if isinstance(expected_config, Mapping) else {}
    current_config = config_identity(settings)
    expected_hash = str(expected_config.get("canonical_sha256") or "")
    if expected_hash and current_config["canonical_sha256"] != expected_hash:
        errors.append({"location": "resolved_config", "error": "canonical config fingerprint mismatch"})

    expected_code = manifest.get("code", {})
    expected_code = expected_code if isinstance(expected_code, Mapping) else {}
    current_code = project_git_identity()
    expected_commit = str(expected_code.get("commit") or expected_code.get("git_commit") or "")
    current_commit = str(current_code.get("commit") or current_code.get("git_commit") or "")
    if expected_commit and current_commit and expected_commit != current_commit:
        errors.append({"location": "code.git_commit", "error": "git revision mismatch"})
    if expected_code.get("dirty") is False and current_code.get("dirty") is True:
        errors.append({"location": "code.dirty", "error": "dirty tree violates replay policy"})

    expected_env = manifest.get("environment", {})
    expected_env = expected_env if isinstance(expected_env, Mapping) else {}
    current_env = environment_identity()
    expected_env_hash = str(expected_env.get("fingerprint") or "")
    if expected_env_hash and current_env["fingerprint"] != expected_env_hash:
        errors.append({"location": "environment", "error": "dependency environment fingerprint mismatch"})
    return {"passed": not errors, "errors": errors, "current": {
        "resolved_config": current_config, "code": current_code, "environment": current_env
    }}


def compare_run_manifests(original: Mapping[str, Any], reproduced: Mapping[str, Any]) -> dict[str, Any]:
    stages = {}
    original_stages = original.get("stage_identities", {})
    reproduced_stages = reproduced.get("stage_identities", {})
    if isinstance(original_stages, Mapping) and isinstance(reproduced_stages, Mapping):
        for key in sorted(set(original_stages) | set(reproduced_stages)):
            stages[str(key)] = {
                "original": original_stages.get(key),
                "reproduced": reproduced_stages.get(key),
                "equal": original_stages.get(key) == reproduced_stages.get(key),
            }
    original_artifacts = {
        (str(item.get("role")), str(item.get("name"))): str(item.get("content_sha256"))
        for item in original.get("artifacts", []) if isinstance(item, Mapping)
    }
    reproduced_artifacts = {
        (str(item.get("role")), str(item.get("name"))): str(item.get("content_sha256"))
        for item in reproduced.get("artifacts", []) if isinstance(item, Mapping)
    }
    artifact_diff = []
    for key in sorted(set(original_artifacts) | set(reproduced_artifacts)):
        left, right = original_artifacts.get(key), reproduced_artifacts.get(key)
        if left != right:
            artifact_diff.append({"role": key[0], "name": key[1], "original": left, "reproduced": right})
    return {
        "run_definition_equal": original.get("run_definition_id") == reproduced.get("run_definition_id"),
        "stage_identities": stages,
        "artifact_differences": artifact_diff,
        "equivalent": (
            original.get("run_definition_id") == reproduced.get("run_definition_id")
            and all(row["equal"] for row in stages.values())
            and not artifact_diff
        ),
    }


def _replace_argument(argv: list[str], flag: str, value: str) -> list[str]:
    result = list(argv)
    if flag in result:
        index = result.index(flag)
        if index + 1 >= len(result):
            raise ValueError(f"{flag} is missing its value")
        result[index + 1] = value
    else:
        result.extend([flag, value])
    return result


def frozen_module_command(
    module: str,
    argv: Sequence[str],
    *,
    dataset_version_id: str | None = None,
    output_flag: str | None = None,
) -> dict[str, Any]:
    frozen = [str(item) for item in argv]
    if dataset_version_id:
        frozen = _replace_argument(frozen, "--dataset-ref", dataset_version_id)
    if output_flag:
        frozen = _replace_argument(frozen, output_flag, "{output}")
    return {
        "command": ["{python}", "-m", module, *frozen],
        "working_directory": "{repository}",
        "mutable_alias_resolution": "forbidden",
    }


def load_manifest(store: Path, run_id: str) -> dict[str, Any]:
    payload = json.loads(resolve_run_manifest(store, run_id).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("run manifest root must be an object")
    return payload


def inspect_run(settings: Settings, run_id: str) -> dict[str, Any]:
    path = resolve_run_manifest(store_root(settings), run_id)
    manifest = load_manifest(store_root(settings), run_id)
    return {
        "manifest": manifest,
        "verification": verify_run_manifest(path),
        "runtime_context": verify_runtime_context(settings, manifest),
    }


def execute_reproduction(settings: Settings, manifest: Mapping[str, Any]) -> dict[str, Any]:
    verification = verify_run_manifest(resolve_run_manifest(store_root(settings), str(manifest["run_id"])))
    runtime = verify_runtime_context(settings, manifest)
    if not verification["passed"] or not runtime["passed"]:
        return {"status": "VERIFY_FAILED", "verification": verification, "runtime_context": runtime}

    contract = manifest.get("reproduction", {})
    if not isinstance(contract, Mapping):
        raise ValueError("run manifest reproduction contract is missing")
    raw = contract.get("command")
    if not isinstance(raw, list) or not raw:
        raise ValueError("run manifest has no executable reproduction command")
    output = settings.paths.output / "reproductions" / str(manifest["run_id"])
    command = [
        str(item).replace("{python}", sys.executable).replace("{output}", str(output))
        for item in raw
    ]
    repository = Path(__file__).resolve().parents[3]
    cwd = Path(str(contract.get("working_directory") or "{repository}").replace("{repository}", str(repository)))
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    reproduced_path = output / RUN_MANIFEST_FILE
    diff = None
    if reproduced_path.is_file():
        reproduced = json.loads(reproduced_path.read_text(encoding="utf-8"))
        if isinstance(reproduced, Mapping):
            diff = compare_run_manifests(manifest, reproduced)
    return {
        "status": "EXECUTED" if completed.returncode == 0 else "EXECUTION_FAILED",
        "exit_code": completed.returncode,
        "command": command,
        "output": str(output),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "diff": diff,
    }


def reproduce_run(settings: Settings, run_id: str, *, execute: bool) -> dict[str, Any]:
    manifest = load_manifest(store_root(settings), run_id)
    verification = verify_run_manifest(resolve_run_manifest(store_root(settings), run_id))
    runtime = verify_runtime_context(settings, manifest)
    if not execute:
        return {"mode": "verify-only", "verification": verification, "runtime_context": runtime}
    if not verification["passed"] or not runtime["passed"]:
        return {
            "mode": "execute", "status": "VERIFY_FAILED",
            "verification": verification, "runtime_context": runtime,
        }
    return {
        "mode": "execute", "verification": verification, "runtime_context": runtime,
        "execution": execute_reproduction(settings, manifest),
    }
