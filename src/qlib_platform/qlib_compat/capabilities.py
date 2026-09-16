from __future__ import annotations

import importlib
from collections import Counter
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml


SUPPORT_STATUSES = {"certified", "upstream-native", "not-certified", "unsupported"}


@dataclass(frozen=True)
class Capability:
    capability_id: str
    target: str
    required: bool
    extra: str | None = None
    support_status: str = "upstream-native"
    owner: str = "qlib-compat"
    version: str = ""
    os_matrix: tuple[str, ...] = ()
    python_matrix: tuple[str, ...] = ()
    positive_evidence: tuple[str, ...] = ()
    negative_evidence: tuple[str, ...] = ()
    negative_required: bool = False
    known_deviation: str | None = None


@dataclass(frozen=True)
class CapabilityResult:
    capability_id: str
    target: str
    required: bool
    extra: str | None
    available: bool
    detail: str
    status: str
    support_status: str


def default_manifest_path() -> Path:
    package_root = resources.files("qlib_platform.qlib_compat")
    manifest = package_root.joinpath("manifests").joinpath("qlib-0.9.7.yaml")
    return Path(str(manifest))


def load_capability_manifest(path: str | Path | None = None) -> dict[str, Any]:
    manifest_path = Path(path) if path is not None else default_manifest_path()
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Qlib capability manifest root must be a mapping")
    if int(payload.get("schema_version", 0)) not in {1, 2}:
        raise ValueError("unsupported Qlib capability manifest schema")
    return dict(payload)


def _string_tuple(value: object, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be a sequence")
    return tuple(str(item).strip() for item in value if str(item).strip())


def _parse_capabilities(manifest: Mapping[str, Any]) -> list[Capability]:
    raw = manifest.get("capabilities", [])
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError("capabilities must be a sequence")

    schema_version = int(manifest.get("schema_version", 0))
    defaults_value = manifest.get("matrix_defaults", {})
    if defaults_value is None:
        defaults_value = {}
    if not isinstance(defaults_value, Mapping):
        raise ValueError("matrix_defaults must be a mapping")
    defaults = dict(defaults_value)
    default_evidence_value = defaults.get("evidence", {})
    if default_evidence_value is None:
        default_evidence_value = {}
    if not isinstance(default_evidence_value, Mapping):
        raise ValueError("matrix_defaults.evidence must be a mapping")
    default_evidence = dict(default_evidence_value)

    qlib_version = str(manifest.get("qlib_version", "")).strip()
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

        support_status = str(
            item.get("certification", defaults.get("certification", "upstream-native"))
        ).strip()
        if support_status not in SUPPORT_STATUSES:
            raise ValueError(f"invalid certification status for {capability_id}: {support_status}")

        evidence_value = item.get("evidence", default_evidence)
        if evidence_value is None:
            evidence_value = {}
        if not isinstance(evidence_value, Mapping):
            raise ValueError(f"evidence must be a mapping for {capability_id}")
        evidence = dict(evidence_value)

        owner = str(item.get("owner", defaults.get("owner", "qlib-compat"))).strip()
        version = str(item.get("version", defaults.get("version", qlib_version))).strip()
        os_matrix = _string_tuple(item.get("os", defaults.get("os", ())), field=f"{capability_id}.os")
        python_matrix = _string_tuple(
            item.get("python", defaults.get("python", ())), field=f"{capability_id}.python"
        )
        positive_evidence = _string_tuple(
            evidence.get("positive", default_evidence.get("positive", ())),
            field=f"{capability_id}.evidence.positive",
        )
        negative_evidence = _string_tuple(
            evidence.get("negative", default_evidence.get("negative", ())),
            field=f"{capability_id}.evidence.negative",
        )
        negative_required = bool(item.get("negative_required", defaults.get("negative_required", False)))
        deviation_value = item.get("known_deviation", defaults.get("known_deviation"))
        known_deviation = None if deviation_value is None else str(deviation_value).strip() or None

        if schema_version >= 2:
            if not owner or not version or not os_matrix or not python_matrix:
                raise ValueError(
                    f"capability {capability_id} requires owner/version/os/python matrix metadata"
                )

        seen.add(capability_id)
        parsed.append(
            Capability(
                capability_id=capability_id,
                target=target,
                required=level == "required",
                extra=extra,
                support_status=support_status,
                owner=owner,
                version=version,
                os_matrix=os_matrix,
                python_matrix=python_matrix,
                positive_evidence=positive_evidence,
                negative_evidence=negative_evidence,
                negative_required=negative_required,
                known_deviation=known_deviation,
            )
        )
    return parsed


def materialize_capability_matrix(manifest: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    payload = dict(manifest or load_capability_manifest())
    return [
        {
            "id": capability.capability_id,
            "target": capability.target,
            "level": "required" if capability.required else "optional",
            "extra": capability.extra,
            "certification": capability.support_status,
            "owner": capability.owner,
            "version": capability.version,
            "os": list(capability.os_matrix),
            "python": list(capability.python_matrix),
            "evidence": {
                "positive": list(capability.positive_evidence),
                "negative": list(capability.negative_evidence),
            },
            "negativeRequired": capability.negative_required,
            "knownDeviation": capability.known_deviation,
        }
        for capability in _parse_capabilities(payload)
    ]


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


def _certification_errors(capabilities: Sequence[Capability]) -> list[str]:
    errors: list[str] = []
    for capability in capabilities:
        if capability.support_status == "certified" and not capability.positive_evidence:
            errors.append(f"{capability.capability_id}: certified capability has no positive evidence")
        if capability.negative_required and not capability.negative_evidence:
            errors.append(f"{capability.capability_id}: required negative evidence is missing")
        if capability.support_status == "unsupported" and not capability.known_deviation:
            errors.append(f"{capability.capability_id}: unsupported capability requires known_deviation")
    return errors


def check_capabilities(
    manifest: Mapping[str, Any] | None = None,
    *,
    require_extras: Sequence[str] = (),
    allow_version_drift: bool = False,
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
    capabilities = _parse_capabilities(payload)
    certification_errors = _certification_errors(capabilities)
    results: list[CapabilityResult] = []
    for capability in capabilities:
        required = capability.required or (capability.extra in required_extras)
        available, detail = _probe_target(capability.target)
        if capability.support_status == "unsupported":
            status = "UNSUPPORTED"
        elif available and capability.support_status == "certified":
            status = "CERTIFIED"
        elif available and capability.support_status == "not-certified":
            status = "NOT_CERTIFIED"
        elif available:
            status = "AVAILABLE"
        elif required:
            status = "FAIL"
        else:
            status = "UNAVAILABLE"
        results.append(
            CapabilityResult(
                capability.capability_id,
                capability.target,
                required,
                capability.extra,
                available,
                detail,
                status,
                capability.support_status,
            )
        )

    failures = [result for result in results if result.status == "FAIL"]
    exceptions = payload.get("known_upstream_exceptions", [])
    if not isinstance(exceptions, list):
        raise ValueError("known_upstream_exceptions must be a list")

    status_counts = Counter(result.status for result in results)
    certifiable = [capability for capability in capabilities if capability.support_status != "unsupported"]
    certified = [capability for capability in certifiable if capability.support_status == "certified"]
    coverage_ratio = len(certified) / len(certifiable) if certifiable else 0.0
    certification_value = payload.get("certification_gate", {})
    if certification_value is None:
        certification_value = {}
    if not isinstance(certification_value, Mapping):
        raise ValueError("certification_gate must be a mapping")
    full_threshold = float(certification_value.get("full_compatibility_threshold", 1.0))
    all_certifiable_certified = bool(certifiable) and len(certified) == len(certifiable)
    full_claim_eligible = (
        version_ok
        and not failures
        and not certification_errors
        and all_certifiable_certified
        and coverage_ratio >= full_threshold
        and all(result.available for result in results if result.support_status == "certified")
    )

    return {
        "contract": str(payload.get("contract", "qlib-native-superset-v1")),
        "expectedQlibVersion": expected_version,
        "actualQlibVersion": actual_version,
        "versionPassed": version_ok,
        "versionDetail": version_detail,
        "allowVersionDrift": allow_version_drift,
        "driftDetected": (not version_ok) or bool(failures),
        "requiredExtras": sorted(required_extras),
        "knownUpstreamExceptions": exceptions,
        "passed": (version_ok or allow_version_drift) and not failures and not certification_errors,
        "requiredFailures": [result.capability_id for result in failures],
        "certificationErrors": certification_errors,
        "certificationSummary": {
            "declaredCapabilities": len(capabilities),
            "certifiableCapabilities": len(certifiable),
            "certifiedCapabilities": len(certified),
            "certifiedCoverage": coverage_ratio,
            "fullCompatibilityThreshold": full_threshold,
            "fullCompatibilityClaimEligible": full_claim_eligible,
            "runtimeStatusCounts": dict(sorted(status_counts.items())),
        },
        "matrix": materialize_capability_matrix(payload),
        "results": [
            {
                "id": result.capability_id,
                "target": result.target,
                "required": result.required,
                "extra": result.extra,
                "available": result.available,
                "detail": result.detail,
                "status": result.status,
                "certification": result.support_status,
            }
            for result in results
        ],
    }
