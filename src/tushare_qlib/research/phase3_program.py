from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..lineage import sha256_json
from ..store import sha256_file
from .phase3_contract import load_phase3_lock


def _write_immutable(payload: dict[str, Any], output: str | Path, identity_key: str) -> Path:
    payload[identity_key] = sha256_json(payload)
    target = Path(output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        if existing != payload:
            raise ValueError(f"existing {payload['schemaVersion']} artifact differs")
        return target
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return target


def write_phase3_experiment_plan(*, contract_lock: str | Path, output: str | Path) -> Path:
    source = Path(contract_lock).expanduser().resolve()
    lock = load_phase3_lock(source)
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
        "schemaVersion": "phase3_diagnostic_plan_v1",
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
    return _write_immutable(payload, output, "planSha256")
