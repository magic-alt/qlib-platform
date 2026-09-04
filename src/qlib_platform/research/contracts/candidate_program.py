from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, cast

import yaml

from qlib_platform.lineage import sha256_json
from qlib_platform.data.store import sha256_file


PHASE2_SCHEMA = "ashare_phase2_v1"
PHASE1_SCHEMA = "alpha_phase1_synthesis_v1"
ALLOWED_DIRECTIONS = {"positive", "negative"}
ALLOWED_KINDS = {"feature", "composite", "interaction", "overlay", "portfolio"}
ALLOWED_STATUSES = {"REJECTED", "RESEARCH_CANDIDATE", "SELECTED_FOR_FINAL_HOLDOUT"}
RECOMMENDATION_ROUTES: dict[str, tuple[str, ...]] = {
    "ALPHA_PACK_V2": ("DATA_RELEASE_V2", "BENCHMARK_FACTORS", "ALPHA_CANDIDATES", "INCREMENTAL_ACCEPTANCE"),
    "REGIME_AWARE_RESEARCH": (
        "DATA_RELEASE_V2",
        "BENCHMARK_FACTORS",
        "ALPHA_CANDIDATES",
        "INCREMENTAL_ACCEPTANCE",
        "REGIME_OVERLAY",
    ),
    "PORTFOLIO_CONSTRUCTION": (
        "PORTFOLIO_IMPLEMENTATION",
        "DATA_RELEASE_V2",
        "BENCHMARK_FACTORS",
        "ALPHA_CANDIDATES",
        "INCREMENTAL_ACCEPTANCE",
    ),
    "XGBOOST_TUNING": ("BOUNDED_XGBOOST_TUNING",),
    "NO_GO_NEW_ALPHA": ("BLOCKED_NO_GO_REPORT",),
}


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _unit(value: object, name: str) -> float:
    result = float(str(value))
    if not 0 < result <= 1:
        raise ValueError(f"{name} must be in (0, 1]")
    return result


def _positive_int(value: object, name: str) -> int:
    result = int(str(value))
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


@dataclass(frozen=True)
class HypothesisSpec:
    hypothesis_id: str
    name: str
    family: str
    kind: str
    direction: str
    definition: str
    normalization: str
    universe: str
    controls: tuple[str, ...]
    members: tuple[str, ...]
    applicable_industries: tuple[str, ...]
    first_rejection: str


@dataclass(frozen=True)
class MultipleTestingSpec:
    hac_lag: int
    bh_alpha: float
    local_fdr_alpha: float
    romano_wolf_alpha: float
    romano_wolf_resamples: int
    block_sessions: int
    random_seed: int


@dataclass(frozen=True)
class RobustnessSpec:
    minimum_coverage: float
    minimum_oriented_rank_ic: float
    minimum_positive_fold_ratio: float
    minimum_hac_t: float
    minimum_worst_fold_rank_ic: float
    minimum_worst_rolling_rank_ic: float
    minimum_leave_one_year_retention: float
    maximum_turnover_increase: float
    stressed_cost_multiple: float


@dataclass(frozen=True)
class HoldoutSpec:
    policy: str
    sessions: int
    label_maturity_sessions: int
    access_limit: int


@dataclass(frozen=True)
class Phase2Contract:
    program_id: str
    data_release_profile: str
    universe: str
    label_spec: str
    split_profile: str
    cost_model: str
    portfolio_policy: str
    hypotheses: tuple[HypothesisSpec, ...]
    multiple_testing: MultipleTestingSpec
    robustness: RobustnessSpec
    holdout: HoldoutSpec
    file_sha256: str
    semantic_sha256: str

    def to_manifest(self) -> dict[str, Any]:
        payload = {
            **asdict(self),
            "hypotheses": [asdict(item) for item in self.hypotheses],
            "multiple_testing": asdict(self.multiple_testing),
            "robustness": asdict(self.robustness),
            "holdout": asdict(self.holdout),
        }
        return cast(dict[str, Any], json.loads(json.dumps(payload, ensure_ascii=False, sort_keys=True)))


def load_candidate_contract(path: str | Path) -> Phase2Contract:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Phase 2 contract is missing: {source}")
    raw = _mapping(yaml.safe_load(source.read_text(encoding="utf-8")), "Phase 2 contract")
    if raw.get("schema") != PHASE2_SCHEMA:
        raise ValueError(f"unsupported Phase 2 schema: {raw.get('schema')}")
    identity = _mapping(raw.get("identity"), "identity")
    program_id = str(identity.get("programId") or "").strip()
    if not program_id:
        raise ValueError("identity.programId is required")
    hypotheses_raw = _mapping(raw.get("hypotheses"), "hypotheses")
    hypotheses: list[HypothesisSpec] = []
    seen_names: set[str] = set()
    for hypothesis_id, value in hypotheses_raw.items():
        item = _mapping(value, f"hypothesis {hypothesis_id}")
        key = str(hypothesis_id).strip()
        name = str(item.get("name") or "").strip()
        kind = str(item.get("kind") or "feature").strip()
        direction = str(item.get("direction") or "").strip()
        if not key or not name or name in seen_names:
            raise ValueError("hypothesis IDs and names must be unique and non-empty")
        if kind not in ALLOWED_KINDS:
            raise ValueError(f"hypothesis {key} has unsupported kind: {kind}")
        if direction not in ALLOWED_DIRECTIONS:
            raise ValueError(f"hypothesis {key} must have a pre-registered direction")
        definition = str(item.get("definition") or "").strip()
        first_rejection = str(item.get("firstRejection") or "").strip()
        if not definition or not first_rejection:
            raise ValueError(f"hypothesis {key} requires definition and firstRejection")
        seen_names.add(name)
        hypotheses.append(
            HypothesisSpec(
                hypothesis_id=key,
                name=name,
                family=str(item.get("family") or "").strip(),
                kind=kind,
                direction=direction,
                definition=definition,
                normalization=str(item.get("normalization") or "daily_rank_z").strip(),
                universe=str(item.get("universe") or identity.get("universe") or "").strip(),
                controls=tuple(str(value) for value in item.get("controls", ())),
                members=tuple(str(value) for value in item.get("members", ())),
                applicable_industries=tuple(
                    str(value) for value in item.get("applicableIndustries", ("ALL",))
                ),
                first_rejection=first_rejection,
            )
        )
    if not hypotheses:
        raise ValueError("at least one hypothesis is required")

    testing = _mapping(raw.get("multipleTesting"), "multipleTesting")
    multiple_testing = MultipleTestingSpec(
        hac_lag=_positive_int(testing.get("hacLag"), "hacLag"),
        bh_alpha=_unit(testing.get("bhAlpha"), "bhAlpha"),
        local_fdr_alpha=_unit(testing.get("localFdrAlpha"), "localFdrAlpha"),
        romano_wolf_alpha=_unit(testing.get("romanoWolfAlpha"), "romanoWolfAlpha"),
        romano_wolf_resamples=_positive_int(testing.get("romanoWolfResamples"), "romanoWolfResamples"),
        block_sessions=_positive_int(testing.get("blockSessions"), "blockSessions"),
        random_seed=int(str(testing.get("randomSeed"))),
    )
    robustness_raw = _mapping(raw.get("robustness"), "robustness")
    robustness = RobustnessSpec(
        minimum_coverage=_unit(robustness_raw.get("minimumCoverage"), "minimumCoverage"),
        minimum_oriented_rank_ic=float(str(robustness_raw.get("minimumOrientedRankIc"))),
        minimum_positive_fold_ratio=_unit(
            robustness_raw.get("minimumPositiveFoldRatio"), "minimumPositiveFoldRatio"
        ),
        minimum_hac_t=float(str(robustness_raw.get("minimumHacT"))),
        minimum_worst_fold_rank_ic=float(str(robustness_raw.get("minimumWorstFoldRankIc"))),
        minimum_worst_rolling_rank_ic=float(str(robustness_raw.get("minimumWorstRollingRankIc"))),
        minimum_leave_one_year_retention=_unit(
            robustness_raw.get("minimumLeaveOneYearRetention"), "minimumLeaveOneYearRetention"
        ),
        maximum_turnover_increase=float(str(robustness_raw.get("maximumTurnoverIncrease"))),
        stressed_cost_multiple=float(str(robustness_raw.get("stressedCostMultiple"))),
    )
    holdout_raw = _mapping(raw.get("finalHoldout"), "finalHoldout")
    holdout = HoldoutSpec(
        policy=str(holdout_raw.get("policy") or ""),
        sessions=_positive_int(holdout_raw.get("sessions"), "finalHoldout.sessions"),
        label_maturity_sessions=_positive_int(
            holdout_raw.get("labelMaturitySessions"), "finalHoldout.labelMaturitySessions"
        ),
        access_limit=_positive_int(holdout_raw.get("accessLimit"), "finalHoldout.accessLimit"),
    )
    if holdout.policy != "FIRST_SESSION_AFTER_SELECTION_LOCK" or holdout.access_limit != 1:
        raise ValueError("Phase 2 final holdout must be future-dated and single access")
    semantic = {
        "schema": PHASE2_SCHEMA,
        "identity": dict(identity),
        "hypotheses": {str(key): dict(value) for key, value in hypotheses_raw.items()},
        "multipleTesting": dict(testing),
        "robustness": dict(robustness_raw),
        "finalHoldout": dict(holdout_raw),
    }
    return Phase2Contract(
        program_id=program_id,
        data_release_profile=str(identity.get("dataReleaseProfile") or ""),
        universe=str(identity.get("universe") or ""),
        label_spec=str(identity.get("labelSpec") or ""),
        split_profile=str(identity.get("splitProfile") or ""),
        cost_model=str(identity.get("costModel") or ""),
        portfolio_policy=str(identity.get("portfolioPolicy") or ""),
        hypotheses=tuple(hypotheses),
        multiple_testing=multiple_testing,
        robustness=robustness,
        holdout=holdout,
        file_sha256=sha256_file(source),
        semantic_sha256=sha256_json(semantic),
    )


def load_phase1_switch(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Phase 1 synthesis manifest is required: {source}")
    manifest = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != PHASE1_SCHEMA:
        raise ValueError("unsupported Phase 1 synthesis manifest")
    status = _mapping(manifest.get("status"), "Phase 1 status")
    if status.get("phase1Completion") not in {"COMPLETE", "COMPLETE_WITH_KNOWN_DATA_GAP"}:
        raise ValueError("Phase 1 is not complete")
    if (
        manifest.get("selectionUsesFinalHoldout") is not False
        or manifest.get("publishingAuthorized") is not False
    ):
        raise ValueError("Phase 1 manifest does not preserve selection/publishing isolation")
    recommendation = str(manifest.get("primaryRecommendation") or "")
    if recommendation not in RECOMMENDATION_ROUTES:
        raise ValueError(f"unsupported Phase 1 recommendation: {recommendation}")
    evidence = manifest.get("evidence", {})
    evidence = evidence if isinstance(evidence, Mapping) else {}
    explanation = evidence.get("modelExplanation", {})
    explanation = explanation if isinstance(explanation, Mapping) else {}
    bounded_sensitivity = str(explanation.get("boundedSensitivity") or "")
    if recommendation == "XGBOOST_TUNING" and bounded_sensitivity != "RECOVERABLE":
        raise ValueError("XGBOOST_TUNING requires RECOVERABLE boundedSensitivity evidence")
    return {
        "path": str(source),
        "studyId": str(manifest.get("studyId") or ""),
        "sha256": sha256_file(source),
        "primaryRecommendation": recommendation,
        "allowedWorkstreams": list(RECOMMENDATION_ROUTES[recommendation]),
        "boundedSensitivity": bounded_sensitivity or None,
    }


def assert_workstream_allowed(lock: Mapping[str, Any], workstream: str) -> None:
    route = _mapping(lock.get("recommendationRoute"), "recommendationRoute")
    allowed = {str(value) for value in route.get("allowedWorkstreams", ())}
    if workstream not in allowed:
        raise PermissionError(f"Phase 1 recommendation does not authorize workstream: {workstream}")


def load_candidate_lock(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Phase 2 contract lock is missing: {source}")
    lock = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(lock, dict) or lock.get("schemaVersion") != "phase2_contract_lock_v1":
        raise ValueError("unsupported Phase 2 contract lock")
    recorded = str(lock.get("lockSha256") or "")
    actual = sha256_json({key: value for key, value in lock.items() if key != "lockSha256"})
    if recorded != actual:
        raise ValueError("Phase 2 contract lock checksum mismatch")
    if lock.get("researchWindow", {}).get("mode") != "ROLLING_OOS_ONLY":
        raise ValueError("Phase 2 contract lock does not seal rolling-OOS-only research")
    return lock


def write_candidate_contract_lock(
    *,
    phase1_manifest: str | Path,
    contract_path: str | Path,
    output: str | Path,
) -> Path:
    phase1 = load_phase1_switch(phase1_manifest)
    contract = load_candidate_contract(contract_path)
    payload: dict[str, Any] = {
        "schemaVersion": "phase2_contract_lock_v1",
        "programId": contract.program_id,
        "phase1": phase1,
        "recommendationRoute": {
            "primaryRecommendation": phase1["primaryRecommendation"],
            "allowedWorkstreams": phase1["allowedWorkstreams"],
        },
        "contract": contract.to_manifest(),
        "candidateStatuses": sorted(ALLOWED_STATUSES),
        "researchWindow": {
            "mode": "ROLLING_OOS_ONLY",
            "finalHoldoutArtifactsAllowed": False,
        },
        "publishingAuthorized": False,
    }
    payload["lockSha256"] = sha256_json(payload)
    target = Path(output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        if existing != payload:
            raise ValueError("existing Phase 2 contract lock differs")
        return target
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return target
