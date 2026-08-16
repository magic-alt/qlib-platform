from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..lineage import sha256_json
from ..store import sha256_file
from .phase2_contract import assert_workstream_allowed, load_phase2_lock
from .phase2_features import EXPERIMENT_MATRIX, feature_set
from .phase2_statistics import evaluate_candidate


_XGB_GRID = (
    {"max_depth": 4, "eta": 0.03, "min_child_weight": 5},
    {"max_depth": 4, "eta": 0.05, "min_child_weight": 10},
    {"max_depth": 6, "eta": 0.03, "min_child_weight": 5},
    {"max_depth": 6, "eta": 0.05, "min_child_weight": 10},
    {"max_depth": 8, "eta": 0.03, "min_child_weight": 5},
    {"max_depth": 8, "eta": 0.05, "min_child_weight": 10},
    {"max_depth": 10, "eta": 0.03, "min_child_weight": 5},
    {"max_depth": 10, "eta": 0.05, "min_child_weight": 10},
)


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


def write_phase2_experiment_plan(*, contract_lock: str | Path, output: str | Path) -> Path:
    source = Path(contract_lock).expanduser().resolve()
    lock = load_phase2_lock(source)
    route = lock["recommendationRoute"]
    recommendation = str(route["primaryRecommendation"])
    allowed = tuple(str(value) for value in route["allowedWorkstreams"])
    experiments: list[dict[str, object]] = []

    if "INCREMENTAL_ACCEPTANCE" in allowed:
        assert_workstream_allowed(lock, "INCREMENTAL_ACCEPTANCE")
        for experiment_id, (feature_set_id, model) in EXPERIMENT_MATRIX.items():
            spec = feature_set(feature_set_id)
            experiments.append(
                {
                    "experimentId": experiment_id,
                    "workstream": "INCREMENTAL_ACCEPTANCE",
                    "alphaPack": spec.source_pack,
                    "featureSet": spec.to_manifest(),
                    "model": model,
                    "mode": "walk-forward",
                    "stage": "release",
                    "usesFinalHoldout": False,
                }
            )
    if "REGIME_OVERLAY" in allowed:
        experiments.extend(
            [
                {
                    "experimentId": "P2-R01",
                    "workstream": "REGIME_OVERLAY",
                    "predictionSnapshotReuse": True,
                    "rule": {"HIGH_VOL_lowvol_weight": 0.5},
                    "usesFinalHoldout": False,
                },
                {
                    "experimentId": "P2-R02",
                    "workstream": "REGIME_OVERLAY",
                    "predictionSnapshotReuse": True,
                    "rule": {"HIGH_VOL_lowvol_weight": 0.0},
                    "usesFinalHoldout": False,
                },
                {
                    "experimentId": "P2-R03",
                    "workstream": "REGIME_OVERLAY",
                    "predictionSnapshotReuse": True,
                    "rule": {"grossExposure": "clip(lagged_expanding_median_vol/lagged_vol,0.5,1.0)"},
                    "usesFinalHoldout": False,
                },
            ]
        )
    if "PORTFOLIO_IMPLEMENTATION" in allowed:
        experiments.insert(
            0,
            {
                "experimentId": "P2-PC01",
                "workstream": "PORTFOLIO_IMPLEMENTATION",
                "predictionSnapshotReuse": True,
                "rule": {"entryRank": 20, "exitRank": 40, "maxReplacements": 5},
                "usesFinalHoldout": False,
            },
        )
    if "BOUNDED_XGBOOST_TUNING" in allowed:
        assert_workstream_allowed(lock, "BOUNDED_XGBOOST_TUNING")
        if lock.get("phase1", {}).get("boundedSensitivity") != "RECOVERABLE":
            raise ValueError("bounded XGBoost plan requires RECOVERABLE Phase 1 evidence")
        experiments = [
            {
                "experimentId": f"P2-XGB-{position:02d}",
                "workstream": "BOUNDED_XGBOOST_TUNING",
                "model": "xgboost",
                "parameters": parameters,
                "mode": "walk-forward",
                "usesFinalHoldout": False,
            }
            for position, parameters in enumerate(_XGB_GRID, start=1)
        ]
    if "BLOCKED_NO_GO_REPORT" in allowed:
        experiments = []

    payload: dict[str, Any] = {
        "schemaVersion": "phase2_experiment_plan_v1",
        "programId": lock["programId"],
        "contractLock": {
            "path": str(source),
            "sha256": sha256_file(source),
            "lockSha256": lock["lockSha256"],
        },
        "recommendation": recommendation,
        "executionOrder": allowed,
        "experiments": experiments,
        "experimentCount": len(experiments),
        "researchWindow": "ROLLING_OOS_ONLY",
        "publishingAuthorized": False,
    }
    return _write_immutable(payload, output, "planSha256")


def write_incremental_acceptance(
    *,
    contract_lock: str | Path,
    candidates: Sequence[Mapping[str, object]],
    output: str | Path,
) -> Path:
    source = Path(contract_lock).expanduser().resolve()
    lock = load_phase2_lock(source)
    assert_workstream_allowed(lock, "INCREMENTAL_ACCEPTANCE")
    registered = {str(item["hypothesis_id"]) for item in lock["contract"].get("hypotheses", ())}
    testing = lock["contract"]["multiple_testing"]
    robustness = lock["contract"]["robustness"]

    from .phase2_contract import MultipleTestingSpec, RobustnessSpec

    testing_spec = MultipleTestingSpec(**testing)
    robustness_spec = RobustnessSpec(**robustness)
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    design_fields = ("alphaPack", "featureSet", "model", "portfolio", "regimeRule")
    for raw in candidates:
        candidate_id = str(raw.get("candidateId") or "").strip()
        hypothesis_id = str(raw.get("hypothesisId") or "").strip()
        metrics = raw.get("metrics")
        if not candidate_id or candidate_id in seen:
            raise ValueError("candidate IDs must be unique and non-empty")
        if hypothesis_id not in registered:
            raise ValueError(f"candidate uses unregistered hypothesis: {hypothesis_id}")
        if not isinstance(metrics, Mapping):
            raise ValueError(f"candidate {candidate_id} metrics must be a mapping")
        if missing := [name for name in design_fields if not str(raw.get(name) or "").strip()]:
            raise ValueError(f"candidate {candidate_id} is missing frozen design fields: {missing}")
        seen.add(candidate_id)
        decision = evaluate_candidate(metrics, multiple_testing=testing_spec, robustness=robustness_spec)
        rows.append(
            {
                "candidateId": candidate_id,
                "hypothesisId": hypothesis_id,
                "status": decision.status,
                "gatePass": decision.passed,
                "rejectionReasons": list(decision.rejection_reasons),
                "metrics": dict(metrics),
                **{name: raw[name] for name in design_fields},
            }
        )
    payload: dict[str, Any] = {
        "schemaVersion": "phase2_incremental_acceptance_v1",
        "programId": lock["programId"],
        "contractLockSha256": lock["lockSha256"],
        "candidates": sorted(rows, key=lambda item: str(item["candidateId"])),
        "acceptedCount": sum(bool(item["gatePass"]) for item in rows),
        "selectionUsesFinalHoldout": False,
        "publishingAuthorized": False,
    }
    return _write_immutable(payload, output, "acceptanceSha256")
