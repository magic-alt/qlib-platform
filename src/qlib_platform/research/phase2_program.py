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

PHASE2_INCREMENTAL_CANDIDATE_FAMILY = (
    "H001",
    "H002",
    "H003",
    "H004",
    "H005",
    "H101",
    "H102",
    "H103",
    "H104",
    "H105",
    "H106",
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
    candidates: Sequence[Mapping[str, object]] | None = None,
    candidate_metrics: str | Path | None = None,
    output: str | Path,
) -> Path:
    source = Path(contract_lock).expanduser().resolve()
    lock = load_phase2_lock(source)
    assert_workstream_allowed(lock, "INCREMENTAL_ACCEPTANCE")
    collector_binding: dict[str, object] | None = None
    if candidate_metrics is not None:
        if candidates is not None:
            raise ValueError("provide candidate_metrics or candidates, not both")
        metrics_path = Path(candidate_metrics).expanduser().resolve()
        raw_metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        if not isinstance(raw_metrics, Mapping):
            raise ValueError("Phase 2 candidate metrics must be a JSON object")
        if raw_metrics.get("schemaVersion") != "phase2_candidate_metrics_v1":
            raise ValueError("unsupported Phase 2 candidate metrics schema")
        recorded_collector_sha = str(raw_metrics.get("collectorSha256") or "")
        actual_collector_sha = sha256_json(
            {key: value for key, value in raw_metrics.items() if key != "collectorSha256"}
        )
        if recorded_collector_sha != actual_collector_sha:
            raise ValueError("Phase 2 candidate metrics checksum mismatch")
        metrics_lock = raw_metrics.get("contractLock")
        if not isinstance(metrics_lock, Mapping) or metrics_lock.get("lockSha256") != lock["lockSha256"]:
            raise ValueError("Phase 2 candidate metrics contract lock mismatch")
        evidence_binding = raw_metrics.get("evidenceIndex")
        if not isinstance(evidence_binding, Mapping):
            raise ValueError("Phase 2 candidate metrics evidence binding is missing")
        evidence_raw = str(evidence_binding.get("path") or "").strip()
        if not evidence_raw:
            raise ValueError("Phase 2 candidate metrics evidence path is missing")
        evidence_source = Path(evidence_raw).expanduser()
        evidence_path = (
            evidence_source if evidence_source.is_absolute() else metrics_path.parent / evidence_source
        ).resolve()
        if not evidence_path.is_file() or sha256_file(evidence_path) != evidence_binding.get("sha256"):
            raise ValueError("Phase 2 candidate metrics evidence checksum mismatch")
        raw_candidates = raw_metrics.get("candidates")
        if not isinstance(raw_candidates, Sequence) or isinstance(raw_candidates, (str, bytes)):
            raise ValueError("Phase 2 candidate metrics candidates must be a sequence")
        candidates = [item for item in raw_candidates if isinstance(item, Mapping)]
        if len(candidates) != len(raw_candidates):
            raise ValueError("Phase 2 candidate metrics contains an invalid candidate")
        collector_binding = {
            "path": str(metrics_path),
            "sha256": sha256_file(metrics_path),
            "collectorSha256": recorded_collector_sha,
            "evidenceIndex": {
                "path": str(evidence_path),
                "sha256": evidence_binding["sha256"],
            },
        }
    if candidates is None:
        raise ValueError("Phase 2 acceptance requires candidate metrics")
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
    if collector_binding is not None:
        actual_family = tuple(sorted(seen))
        if actual_family != PHASE2_INCREMENTAL_CANDIDATE_FAMILY:
            raise ValueError(
                "Phase 2 candidate metrics must contain exactly the frozen incremental candidate family"
            )
        if any(str(item["candidateId"]) != str(item["hypothesisId"]) for item in rows):
            raise ValueError("Phase 2 candidate IDs must match their frozen hypothesis IDs")
    payload: dict[str, Any] = {
        "schemaVersion": "phase2_incremental_acceptance_v1",
        "programId": lock["programId"],
        "contractLockSha256": lock["lockSha256"],
        "candidates": sorted(rows, key=lambda item: str(item["candidateId"])),
        "acceptedCount": sum(bool(item["gatePass"]) for item in rows),
        "selectionUsesFinalHoldout": False,
        "publishingAuthorized": False,
    }
    if collector_binding is not None:
        payload["candidateMetrics"] = collector_binding
    return _write_immutable(payload, output, "acceptanceSha256")
