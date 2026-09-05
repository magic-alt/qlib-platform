from __future__ import annotations

import importlib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml


@dataclass(frozen=True)
class Capability:
    capability_id: str
    target: str
    required: bool
    extra: str | None = None


@dataclass(frozen=True)
class CapabilityResult:
    capability_id: str
    target: str
    required: bool
    extra: str | None
    available: bool
    detail: str


def default_manifest_path() -> Path:
    package_root = resources.files("qlib_platform.qlib_compat")
    return Path(str(package_root.joinpath("manifests", "qlib-0.9.7.yaml")))


def load_capability_manifest(path: str | Path | None = None) -> dict[str, Any]:
    manifest_path = Path(path) if path is not None else default_manifest_path()
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Qlib capability manifest root must be a mapping")
    if int(payload.get("schema_version", 0)) != 1:
        raise ValueError("unsupported Qlib capability manifest schema")
    return dict(payload)


def _parse_capabilities(manifest: Mapping[str, Any]) -> list[Capability]:
    raw = manifest.get("capabilities", [])
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError("capabilities must be a sequence")
    parsed: list[Capability] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("each capability must be a mapping")
        capability_id = str(item.get("id", "")).strip()
        target = str(item.get("target", "")).strip()
        level = str(item.get("level", "required")).strip().lower()
        extra_value = item.get("extra")
        extra = None if extra_value is None else str(extra_value).strip() or None
        if not capability_id or not target:
            raise ValueError("capability id and target are required")
        if capability_id in seen:
            raise ValueError(f"duplicate capability id: {capability_id}")
        if level not in {"required", "optional"}:
            raise ValueError(f"invalid capability level for {capability_id}: {level}")
        seen.add(capability_id)
        parsed.append(Capability(capability_id, target, level == "required", extra))
    return parsed


def _probe_target(target: str) -> tuple[bool, str]:
    module_name, separator, attribute_path = target.partition(":")
    try:
        value: object = importlib.import_module(module_name)
        if separator:
            for attribute in attribute_path.split("."):
                value = getattr(value, attribute)
        if value is None:
            return False, "target resolved to None"
        return True, "available"
    except (ImportError, AttributeError, ModuleNotFoundError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


def check_capabilities(
    manifest: Mapping[str, Any] | None = None,
    *,
    require_extras: Sequence[str] = (),
) -> dict[str, Any]:
    payload = dict(manifest or load_capability_manifest())
    expected_version = str(payload.get("qlib_version", "")).strip()
    if not expected_version:
        raise ValueError("capability manifest requires qlib_version")

    try:
        import qlib

        actual_version = str(qlib.__version__)
    except (ImportError, AttributeError) as exc:
        actual_version = "unavailable"
        version_ok = False
        version_detail = f"{type(exc).__name__}: {exc}"
    else:
        version_ok = actual_version == expected_version
        version_detail = "matched" if version_ok else f"expected {expected_version}, got {actual_version}"

    required_extras = {str(value).strip() for value in require_extras if str(value).strip()}
    results: list[CapabilityResult] = []
    for capability in _parse_capabilities(payload):
        required = capability.required or (capability.extra in required_extras)
        available, detail = _probe_target(capability.target)
        results.append(
            CapabilityResult(
                capability.capability_id,
                capability.target,
                required,
                capability.extra,
                available,
                detail,
            )
        )

    failures = [result for result in results if result.required and not result.available]
    return {
        "contract": str(payload.get("contract", "qlib-native-superset-v1")),
        "expectedQlibVersion": expected_version,
        "actualQlibVersion": actual_version,
        "versionPassed": version_ok,
        "versionDetail": version_detail,
        "requiredExtras": sorted(required_extras),
        "passed": version_ok and not failures,
        "requiredFailures": [result.capability_id for result in failures],
        "results": [
            {
                "id": result.capability_id,
                "target": result.target,
                "required": result.required,
                "extra": result.extra,
                "available": result.available,
                "detail": result.detail,
            }
            for result in results
        ],
    }
