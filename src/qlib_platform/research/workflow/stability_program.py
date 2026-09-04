from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from qlib_platform.lineage import sha256_json
from qlib_platform.data.store import sha256_file
from qlib_platform.research.artifacts.io import write_immutable_json
from qlib_platform.research.contracts.stability_program import load_stability_lock


STABILITY_PLAN_SCHEMA = "phase3_diagnostic_plan_v1"
STABILITY_EXECUTION_ORDER = ("P3-D00", "P3-D01", "P3-D02", "P3-D03", "P3-D04")


def load_stability_plan(path: str | Path, *, contract_lock_sha256: str | None = None) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Phase 3 diagnostic plan is missing: {source}")
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schemaVersion") != STABILITY_PLAN_SCHEMA:
        raise ValueError("unsupported Phase 3 diagnostic plan")
    recorded = str(payload.get("planSha256") or "")
    actual = sha256_json({key: value for key, value in payload.items() if key != "planSha256"})
    if recorded != actual:
        raise ValueError("Phase 3 diagnostic plan checksum mismatch")
    lock = payload.get("contractLock")
    if not isinstance(lock, dict) or not str(lock.get("lockSha256") or ""):
        raise ValueError("Phase 3 diagnostic plan contract-lock binding is missing")
    if contract_lock_sha256 is not None and lock.get("lockSha256") != contract_lock_sha256:
        raise ValueError("Phase 3 diagnostic plan uses a different design lock")
    if tuple(payload.get("executionOrder", ())) != STABILITY_EXECUTION_ORDER:
        raise ValueError("Phase 3 diagnostic plan execution order drift")
    workstreams = payload.get("workstreams")
    if not isinstance(workstreams, list) or len(workstreams) != len(STABILITY_EXECUTION_ORDER):
        raise ValueError("Phase 3 diagnostic plan workstream set drift")
    if (
        tuple(item.get("workstreamId") for item in workstreams if isinstance(item, dict))
        != STABILITY_EXECUTION_ORDER
    ):
        raise ValueError("Phase 3 diagnostic plan workstream set drift")
    if any(
        item.get("requiresRetraining") is not False or item.get("producesFormalCandidate") is not False
        for item in workstreams
        if isinstance(item, dict)
    ):
        raise ValueError("Phase 3 diagnostic plan authorizes retraining or candidates")
    if (
        payload.get("stateBefore") != "PHASE3_DESIGN_LOCKED"
        or payload.get("stateAfter") != "PHASE3_DIAGNOSIS_COMPLETE"
        or payload.get("diagnosisOnly") is not True
        or payload.get("confirmationHypotheses") != []
        or payload.get("finalHoldoutAccessAllowed") is not False
        or payload.get("publishingAuthorized") is not False
    ):
        raise ValueError("Phase 3 diagnostic plan isolation state drift")
    return payload


def write_stability_experiment_plan(*, contract_lock: str | Path, output: str | Path) -> Path:
    source = Path(contract_lock).expanduser().resolve()
    lock = load_stability_lock(source)
    contract = lock["contract"]
    diagnostics = contract["diagnostics"]
    workstreams: list[dict[str, object]] = [
        {
            "workstreamId": "P3-D00",
            "name": "Contract and lineage freeze",
            "requiresRetraining": False,
            "source": "phase2_immutable_evidence",
            "producesFormalCandidate": False,
        },
        {
            "workstreamId": "P3-D01",
            "name": "Alpha failure map",
            "requiresRetraining": False,
            "rollingWindows": diagnostics["rolling_windows"],
            "producesFormalCandidate": False,
        },
        {
            "workstreamId": "P3-D02",
            "name": "Causal regime attribution",
            "requiresRetraining": False,
            "regimeSpec": lock["lineage"]["regimeSpec"],
            "producesFormalCandidate": False,
        },
        {
            "workstreamId": "P3-D03",
            "name": "Regime transition risk",
            "requiresRetraining": False,
            "transitionWindows": diagnostics["transition_windows"],
            "producesFormalCandidate": False,
        },
        {
            "workstreamId": "P3-D04",
            "name": "Model aging and signal decay",
            "requiresRetraining": False,
            "ageBucketUpperSessions": diagnostics["age_bucket_upper_sessions"],
            "producesFormalCandidate": False,
        },
    ]
    payload: dict[str, Any] = {
        "schemaVersion": STABILITY_PLAN_SCHEMA,
        "programId": lock["programId"],
        "contractLock": {
            "path": str(source),
            "sha256": sha256_file(source),
            "lockSha256": lock["lockSha256"],
        },
        "stateBefore": "PHASE3_DESIGN_LOCKED",
        "stateAfter": "PHASE3_DIAGNOSIS_COMPLETE",
        "executionOrder": [item["workstreamId"] for item in workstreams],
        "workstreams": workstreams,
        "anchors": contract["anchors"],
        "comparisons": contract["comparisons"],
        "researchWindow": "ROLLING_OOS_ONLY",
        "diagnosisOnly": True,
        "confirmationHypotheses": [],
        "finalHoldoutAccessAllowed": False,
        "publishingAuthorized": False,
    }
    return write_immutable_json(payload, output, "planSha256")
