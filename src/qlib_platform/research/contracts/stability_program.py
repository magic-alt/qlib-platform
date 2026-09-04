from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

import pandas as pd
import yaml

from qlib_platform.lineage import git_revision, sha256_json
from qlib_platform.artifacts.prediction_snapshot import load_prediction_snapshot
from qlib_platform.data.store import sha256_file
from qlib_platform.research.features.candidate_sets import EXPERIMENT_MATRIX, feature_set
from qlib_platform.research.evidence.data_acceptance import REQUIRED_V2_ACCEPTANCE_CHECKS
from qlib_platform.research.workflow.candidate_program import INCREMENTAL_CANDIDATE_FAMILY
from qlib_platform.research.diagnostics.regimes import load_regime_spec


PHASE3_SCHEMA = "ashare_phase3_v1"
PHASE3_LOCK_SCHEMA = "phase3_design_lock_v1"
PHASE2_ACCEPTANCE_SCHEMA = "phase2_incremental_acceptance_v1"
PHASE2_EVIDENCE_SCHEMA = "phase2_evidence_index_v1"


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _sequence(value: object, name: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must be a sequence")
    return value


def _positive_int(value: object, name: str) -> int:
    parsed = int(str(value))
    if parsed < 1:
        raise ValueError(f"{name} must be positive")
    return parsed


def _load_json(path: Path, name: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"{name} is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{name} must be a JSON object")
    return payload


def _resolve(base: Path, value: object, name: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{name} path is required")
    source = Path(raw).expanduser()
    target = (source if source.is_absolute() else base / source).resolve()
    if not target.exists():
        raise FileNotFoundError(f"{name} is missing: {target}")
    return target


@dataclass(frozen=True)
class Phase3AnchorSpec:
    anchor_id: str
    experiment_id: str
    role: str
    feature_set_id: str
    model: str


@dataclass(frozen=True)
class Phase3ComparisonSpec:
    candidate: str
    baseline: str


@dataclass(frozen=True)
class Phase3DiagnosticSpec:
    rolling_windows: tuple[int, ...]
    transition_windows: tuple[int, ...]
    minimum_regime_sessions: int
    minimum_cross_section: int
    topk: int
    age_bucket_upper_sessions: tuple[int, ...]


@dataclass(frozen=True)
class Phase3Contract:
    program_id: str
    predecessor_program: str
    universe: str
    label_spec: str
    cost_model: str
    portfolio_policy: str
    objective: str
    anchors: tuple[Phase3AnchorSpec, ...]
    comparisons: tuple[Phase3ComparisonSpec, ...]
    regime_path: str
    diagnostics: Phase3DiagnosticSpec
    file_sha256: str
    semantic_sha256: str

    def to_manifest(self) -> dict[str, Any]:
        payload = {
            **asdict(self),
            "anchors": [asdict(item) for item in self.anchors],
            "comparisons": [asdict(item) for item in self.comparisons],
            "diagnostics": asdict(self.diagnostics),
        }
        return cast(dict[str, Any], json.loads(json.dumps(payload, ensure_ascii=False, sort_keys=True)))


def load_stability_contract(path: str | Path) -> Phase3Contract:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Phase 3 contract is missing: {source}")
    raw = _mapping(yaml.safe_load(source.read_text(encoding="utf-8")), "Phase 3 contract")
    if raw.get("schema") != PHASE3_SCHEMA:
        raise ValueError(f"unsupported Phase 3 schema: {raw.get('schema')}")

    identity = _mapping(raw.get("identity"), "identity")
    objective = _mapping(raw.get("objective"), "objective")
    if objective.get("primary") != "TEMPORAL_ALPHA_STABILITY":
        raise ValueError("Phase 3 objective must be TEMPORAL_ALPHA_STABILITY")
    if objective.get("factorExpansionAllowed") is not False:
        raise ValueError("Phase 3-D must prohibit factor expansion")
    if objective.get("technicalZooExpansionAllowed") is not False:
        raise ValueError("Phase 3-D must prohibit technical-zoo expansion")

    anchors_raw = _sequence(raw.get("anchors"), "anchors")
    anchors: list[Phase3AnchorSpec] = []
    for position, value in enumerate(anchors_raw):
        item = _mapping(value, f"anchor {position}")
        experiment_id = str(item.get("experimentId") or "").strip()
        anchor = Phase3AnchorSpec(
            anchor_id=str(item.get("id") or "").strip(),
            experiment_id=experiment_id,
            role=str(item.get("role") or "").strip(),
            feature_set_id=str(item.get("featureSet") or "").strip(),
            model=str(item.get("model") or "").strip().lower(),
        )
        if any(not value for value in asdict(anchor).values()):
            raise ValueError("Phase 3 anchors require id, experimentId, role, featureSet, and model")
        expected = EXPERIMENT_MATRIX.get(experiment_id)
        if expected != (anchor.feature_set_id, anchor.model):
            raise ValueError(f"Phase 3 anchor {anchor.anchor_id} drifts from {experiment_id}")
        anchors.append(anchor)
    anchor_ids = [item.anchor_id for item in anchors]
    if not anchors or len(anchor_ids) != len(set(anchor_ids)):
        raise ValueError("Phase 3 anchor IDs must be non-empty and unique")

    comparisons_raw = _sequence(raw.get("comparisons"), "comparisons")
    comparisons = tuple(
        Phase3ComparisonSpec(
            candidate=str(_mapping(value, "comparison").get("candidate") or "").strip(),
            baseline=str(_mapping(value, "comparison").get("baseline") or "").strip(),
        )
        for value in comparisons_raw
    )
    for comparison in comparisons:
        if (
            not comparison.candidate
            or not comparison.baseline
            or comparison.candidate == comparison.baseline
            or comparison.candidate not in anchor_ids
            or comparison.baseline not in anchor_ids
        ):
            raise ValueError("Phase 3 comparisons must reference two distinct registered anchors")

    regime_raw = _mapping(raw.get("regime"), "regime")
    regime_path = str(regime_raw.get("spec") or "").strip()
    if not regime_path:
        raise ValueError("regime.spec is required")
    diagnostics_raw = _mapping(raw.get("diagnostics"), "diagnostics")
    rolling_windows = tuple(
        _positive_int(value, "rollingWindows")
        for value in _sequence(diagnostics_raw.get("rollingWindows"), "rollingWindows")
    )
    transition_windows = tuple(
        _positive_int(value, "transitionWindows")
        for value in _sequence(diagnostics_raw.get("transitionWindows"), "transitionWindows")
    )
    age_buckets = tuple(
        _positive_int(value, "ageBucketUpperSessions")
        for value in _sequence(diagnostics_raw.get("ageBucketUpperSessions"), "ageBucketUpperSessions")
    )
    if (
        tuple(sorted(set(rolling_windows))) != rolling_windows
        or tuple(sorted(set(transition_windows))) != transition_windows
        or tuple(sorted(set(age_buckets))) != age_buckets
    ):
        raise ValueError("Phase 3 diagnostic windows and age buckets must be unique and increasing")
    diagnostics = Phase3DiagnosticSpec(
        rolling_windows=rolling_windows,
        transition_windows=transition_windows,
        minimum_regime_sessions=_positive_int(
            diagnostics_raw.get("minimumRegimeSessions"), "minimumRegimeSessions"
        ),
        minimum_cross_section=_positive_int(
            diagnostics_raw.get("minimumCrossSection"), "minimumCrossSection"
        ),
        topk=_positive_int(diagnostics_raw.get("topK"), "topK"),
        age_bucket_upper_sessions=age_buckets,
    )
    if diagnostics.minimum_cross_section < 2 * diagnostics.topk:
        raise ValueError("minimumCrossSection must be at least 2 * topK")

    final_holdout = _mapping(raw.get("finalHoldout"), "finalHoldout")
    if (
        final_holdout.get("inheritedFromPhase2") is not True
        or final_holdout.get("accessAllowed") is not False
    ):
        raise ValueError("Phase 3-D must inherit and seal the Phase 2 final holdout")
    if raw.get("publishingAuthorized") is not False:
        raise ValueError("Phase 3-D cannot authorize publishing")
    if raw.get("formalCandidatesAllowed") is not False:
        raise ValueError("Phase 3-D cannot produce formal candidates")

    semantic = {
        "schema": PHASE3_SCHEMA,
        "identity": dict(identity),
        "objective": dict(objective),
        "anchors": [dict(_mapping(value, "anchor")) for value in anchors_raw],
        "comparisons": [dict(_mapping(value, "comparison")) for value in comparisons_raw],
        "regime": dict(regime_raw),
        "diagnostics": dict(diagnostics_raw),
        "finalHoldout": dict(final_holdout),
        "formalCandidatesAllowed": False,
        "publishingAuthorized": False,
    }
    program_id = str(identity.get("programId") or "").strip()
    predecessor = str(identity.get("predecessorProgram") or "").strip()
    if not program_id or not predecessor:
        raise ValueError("identity.programId and identity.predecessorProgram are required")
    return Phase3Contract(
        program_id=program_id,
        predecessor_program=predecessor,
        universe=str(identity.get("universe") or "").strip(),
        label_spec=str(identity.get("labelSpec") or "").strip(),
        cost_model=str(identity.get("costModel") or "").strip(),
        portfolio_policy=str(identity.get("portfolioPolicy") or "").strip(),
        objective=str(objective["primary"]),
        anchors=tuple(anchors),
        comparisons=comparisons,
        regime_path=regime_path,
        diagnostics=diagnostics,
        file_sha256=sha256_file(source),
        semantic_sha256=sha256_json(semantic),
    )


def _validate_candidate_acceptance(path: Path, predecessor_program: str) -> dict[str, Any]:
    acceptance = _load_json(path, "Phase 2 acceptance")
    if acceptance.get("schemaVersion") != PHASE2_ACCEPTANCE_SCHEMA:
        raise ValueError(f"unsupported Phase 2 acceptance: {acceptance.get('schemaVersion')}")
    recorded = str(acceptance.get("acceptanceSha256") or "")
    actual = sha256_json({key: value for key, value in acceptance.items() if key != "acceptanceSha256"})
    if recorded != actual:
        raise ValueError("Phase 2 acceptance checksum mismatch")
    if acceptance.get("programId") != predecessor_program:
        raise ValueError("Phase 2 acceptance program does not match Phase 3 predecessor")
    candidates = _sequence(acceptance.get("candidates"), "Phase 2 acceptance candidates")
    candidate_rows = [_mapping(item, "Phase 2 acceptance candidate") for item in candidates]
    candidate_ids = tuple(sorted(str(item.get("candidateId") or "") for item in candidate_rows))
    hypothesis_ids = tuple(sorted(str(item.get("hypothesisId") or "") for item in candidate_rows))
    if (
        candidate_ids != INCREMENTAL_CANDIDATE_FAMILY
        or hypothesis_ids != INCREMENTAL_CANDIDATE_FAMILY
        or any(item.get("candidateId") != item.get("hypothesisId") for item in candidate_rows)
    ):
        raise ValueError("Phase 3-D requires exactly the frozen Phase 2 candidate family")
    accepted = sum(bool(item.get("gatePass")) for item in candidate_rows)
    if int(acceptance.get("acceptedCount", -1)) != 0 or accepted != 0:
        raise ValueError("Phase 3-D immutable entry condition requires Phase 2 acceptedCount=0")
    if any(item.get("status") != "REJECTED" for item in candidate_rows):
        raise ValueError("Phase 3-D requires every Phase 2 candidate to remain REJECTED")
    if (
        acceptance.get("selectionUsesFinalHoldout") is not False
        or acceptance.get("publishingAuthorized") is not False
    ):
        raise ValueError("Phase 2 acceptance does not preserve holdout/publishing isolation")
    return acceptance


def _validate_candidate_acceptance_provenance(
    acceptance_path: Path,
    acceptance: Mapping[str, Any],
    evidence_path: Path,
) -> dict[str, Any]:
    binding = _mapping(acceptance.get("candidateMetrics"), "Phase 2 candidate-metrics binding")
    metrics_path = _resolve(acceptance_path.parent, binding.get("path"), "Phase 2 candidate metrics")
    if sha256_file(metrics_path) != binding.get("sha256"):
        raise ValueError("Phase 2 candidate-metrics file checksum mismatch")
    metrics = _load_json(metrics_path, "Phase 2 candidate metrics")
    if metrics.get("schemaVersion") != "phase2_candidate_metrics_v1":
        raise ValueError("unsupported Phase 2 candidate-metrics schema")
    if metrics.get("programId") != acceptance.get("programId"):
        raise ValueError("Phase 2 acceptance/collector program mismatch")
    metrics_lock = _mapping(metrics.get("contractLock"), "collector contract-lock binding")
    if metrics_lock.get("lockSha256") != acceptance.get("contractLockSha256"):
        raise ValueError("Phase 2 acceptance/collector contract-lock mismatch")
    recorded = str(metrics.get("collectorSha256") or "")
    actual = sha256_json({key: value for key, value in metrics.items() if key != "collectorSha256"})
    if recorded != actual or recorded != binding.get("collectorSha256"):
        raise ValueError("Phase 2 candidate-metrics collector checksum mismatch")
    evidence_binding = _mapping(metrics.get("evidenceIndex"), "collector evidence binding")
    if evidence_binding.get("sha256") != sha256_file(evidence_path):
        raise ValueError("Phase 2 collector is not bound to the supplied evidence index")
    acceptance_evidence = _mapping(binding.get("evidenceIndex"), "acceptance evidence binding")
    if acceptance_evidence.get("sha256") != evidence_binding.get("sha256"):
        raise ValueError("Phase 2 acceptance/collector evidence binding mismatch")
    collector_candidates = {
        str(item.get("candidateId") or ""): item
        for item in (
            _mapping(raw, "Phase 2 collector candidate")
            for raw in _sequence(metrics.get("candidates"), "Phase 2 collector candidates")
        )
    }
    acceptance_candidates = {
        str(item.get("candidateId") or ""): item
        for item in (
            _mapping(raw, "Phase 2 acceptance candidate")
            for raw in _sequence(acceptance.get("candidates"), "Phase 2 acceptance candidates")
        )
    }
    if tuple(sorted(collector_candidates)) != INCREMENTAL_CANDIDATE_FAMILY:
        raise ValueError("Phase 2 collector does not contain the frozen candidate family")
    frozen_fields = (
        "candidateId",
        "hypothesisId",
        "metrics",
        "alphaPack",
        "featureSet",
        "model",
        "portfolio",
        "regimeRule",
    )
    for candidate_id in INCREMENTAL_CANDIDATE_FAMILY:
        collector_row = collector_candidates[candidate_id]
        acceptance_row = acceptance_candidates[candidate_id]
        if any(collector_row.get(field) != acceptance_row.get(field) for field in frozen_fields):
            raise ValueError(f"Phase 2 acceptance candidate differs from collector: {candidate_id}")
    return {
        "path": str(metrics_path),
        "sha256": sha256_file(metrics_path),
        "collectorSha256": recorded,
        "evidenceIndexSha256": evidence_binding["sha256"],
    }


def _validate_data_release_acceptance(
    path: Path,
    *,
    data_release_id: str,
    manifest_sha256: str,
) -> dict[str, Any]:
    acceptance = _load_json(path, "Phase 2 DataRelease-v2 acceptance")
    if acceptance.get("schemaVersion") != "phase2_data_release_acceptance_v1":
        raise ValueError("unsupported Phase 2 DataRelease-v2 acceptance schema")
    recorded = str(acceptance.get("acceptanceSha256") or "")
    actual = sha256_json({key: value for key, value in acceptance.items() if key != "acceptanceSha256"})
    if recorded != actual:
        raise ValueError("Phase 2 DataRelease-v2 acceptance checksum mismatch")
    if (
        acceptance.get("dataReleaseId") != data_release_id
        or acceptance.get("manifestSha256") != manifest_sha256
        or acceptance.get("profile") != "ashare_qlib_research_v2"
    ):
        raise ValueError("Phase 2 DataRelease-v2 acceptance does not match the locked DataRelease")
    checks = _mapping(acceptance.get("checks"), "DataRelease-v2 acceptance checks")
    if set(checks) != set(REQUIRED_V2_ACCEPTANCE_CHECKS):
        raise ValueError("Phase 2 DataRelease-v2 acceptance must contain exactly eight required checks")
    for name in REQUIRED_V2_ACCEPTANCE_CHECKS:
        check = _mapping(checks[name], name)
        artifact_sha = str(check.get("artifactSha256") or "").lower()
        if check.get("status") != "PASS":
            raise ValueError("Phase 2 DataRelease-v2 acceptance contains a non-PASS check")
        if len(artifact_sha) != 64 or any(character not in "0123456789abcdef" for character in artifact_sha):
            raise ValueError("Phase 2 DataRelease-v2 acceptance contains an invalid artifact hash")
    if (
        acceptance.get("passed") is not True
        or acceptance.get("publishingAuthorized") is not False
        or acceptance.get("scope") != "NARROW_V2_DELTA_ACCEPTANCE"
        or acceptance.get("fullInfrastructureRecertificationRun") is not False
    ):
        raise ValueError("Phase 2 DataRelease-v2 acceptance state is invalid")
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "acceptanceSha256": recorded,
        "dataReleaseId": data_release_id,
        "manifestSha256": manifest_sha256,
    }


def _contains_final_holdout(manifest: Mapping[str, Any]) -> bool:
    if str(manifest.get("runKind") or "").lower() == "final_holdout":
        return True
    for value in manifest.get("folds", ()):  # type: ignore[union-attr]
        if isinstance(value, Mapping) and (
            value.get("final_holdout") is True or str(value.get("key") or "").lower() == "final_holdout"
        ):
            return True
    return any(manifest.get(key) is True for key in ("usesFinalHoldout", "finalHoldoutUsed"))


def _artifact_path(manifest: Mapping[str, Any], name: str) -> Path:
    for value in manifest.get("artifacts", ()):  # type: ignore[union-attr]
        if isinstance(value, Mapping) and value.get("name") == name:
            target = Path(str(value.get("localPath") or "")).expanduser().resolve()
            if not target.is_file():
                raise FileNotFoundError(f"run artifact is missing: {target}")
            return target
    raise FileNotFoundError(f"run manifest artifact is missing: {name}")


def _source_revision(manifest: Mapping[str, Any]) -> tuple[str, bool | None, bool | None]:
    lineage = _mapping(manifest.get("lineage"), "anchor source-code lineage")
    candidates: list[Mapping[str, Any]] = []
    for key in ("researchContract", "researchSelectionLock"):
        value = manifest.get(key)
        if isinstance(value, Mapping):
            candidates.append(value)
    walk_forward = manifest.get("walkForwardEvidence")
    if isinstance(walk_forward, Mapping):
        selection_lock = walk_forward.get("researchSelectionLock")
        if isinstance(selection_lock, Mapping):
            candidates.append(selection_lock)
    for candidate in candidates:
        commit = str(candidate.get("codeCommit") or "").strip()
        if commit and candidate.get("codeDirty") in {True, False}:
            complete = lineage.get("complete")
            return commit, bool(candidate["codeDirty"]), bool(complete) if complete is not None else None
    commit = str(lineage.get("qlibPlatformCommit") or "").strip()
    dirty = lineage.get("qlibPlatformDirty")
    complete = lineage.get("complete")
    return (
        commit,
        bool(dirty) if dirty in {True, False} else None,
        bool(complete) if complete is not None else None,
    )


def _anchor_lineage(
    evidence_path: Path,
    evidence: Mapping[str, Any],
    anchors: Sequence[Phase3AnchorSpec],
    *,
    data_release_id: str,
    dataset_version_id: str,
    feature_snapshot_id: str,
    label_spec: str,
) -> dict[str, Any]:
    experiments = _mapping(evidence.get("ablationExperiments"), "Phase 2 ablation experiments")
    base = evidence_path.parent
    result: dict[str, Any] = {}
    reference_index: pd.MultiIndex | None = None
    reference_label: pd.Series | None = None
    snapshot_ids: set[str] = set()
    source_commits: set[str] = set()
    for anchor in anchors:
        item = _mapping(experiments.get(anchor.experiment_id), f"anchor {anchor.experiment_id}")
        raw_manifests = _sequence(item.get("runManifests"), f"anchor {anchor.anchor_id} runManifests")
        runs: list[dict[str, Any]] = []
        frames: list[pd.DataFrame] = []
        for value in raw_manifests:
            run_path = _resolve(base, value, f"anchor {anchor.anchor_id} run manifest")
            run = _load_json(run_path, f"anchor {anchor.anchor_id} run manifest")
            if run.get("schemaVersion") != "2.0" or _contains_final_holdout(run):
                raise ValueError(f"anchor {anchor.anchor_id} contains final-holdout or unsupported evidence")
            promotion = _mapping(run.get("promotion"), "anchor promotion")
            if promotion.get("promotionAuthorized") is not False:
                raise ValueError("Phase 3-D anchor evidence cannot authorize promotion")
            source_commit, source_dirty, lineage_complete = _source_revision(run)
            if not source_commit or source_dirty is not False or lineage_complete is not True:
                raise ValueError("Phase 3-D anchor evidence requires clean complete source-code lineage")
            source_commits.add(source_commit)
            experiment = _mapping(run.get("researchExperiment"), "anchor research experiment")
            if (
                experiment.get("feature_set_id") != anchor.feature_set_id
                or str(_mapping(run.get("runtime"), "anchor runtime").get("modelFamily") or "").lower()
                != anchor.model
            ):
                raise ValueError(f"anchor {anchor.anchor_id} feature/model binding drift")
            dataset = _mapping(run.get("dataset"), "anchor dataset")
            store = _mapping(run.get("featureStore"), "anchor FeatureSnapshot")
            if (
                dataset.get("datasetId") != data_release_id
                or str(dataset.get("versionId") or "") != dataset_version_id
            ):
                raise ValueError(f"anchor {anchor.anchor_id} DataRelease/DatasetVersion drift")
            if (
                store.get("featureSnapshotId") != feature_snapshot_id
                or str(store.get("datasetVersionId") or "") != dataset_version_id
            ):
                raise ValueError(f"anchor {anchor.anchor_id} FeatureSnapshot drift")
            snapshot_path = _artifact_path(run, "oos_predictions.snapshot.json")
            frame, snapshot = load_prediction_snapshot(snapshot_path)
            if snapshot != run.get("predictionSnapshot"):
                raise ValueError(f"anchor {anchor.anchor_id} PredictionSnapshot manifest drift")
            contract = _mapping(snapshot.get("contract"), "anchor PredictionSnapshot contract")
            expected = {
                "data_release_id": data_release_id,
                "feature_snapshot_id": feature_snapshot_id,
                "label_spec_id": label_spec,
                "feature_set_id": anchor.feature_set_id,
                "alpha_pack_id": feature_set(anchor.feature_set_id).source_pack,
            }
            if any(contract.get(key) != expected_value for key, expected_value in expected.items()):
                raise ValueError(f"anchor {anchor.anchor_id} PredictionSnapshot contract drift")
            if "final_holdout" in str(contract.get("fold_id") or "").lower():
                raise ValueError(f"anchor {anchor.anchor_id} PredictionSnapshot uses the final holdout")
            runtime = _mapping(run.get("runtime"), "anchor runtime")
            if contract.get("model_profile_id") != runtime.get("modelProfile"):
                raise ValueError(f"anchor {anchor.anchor_id} model-profile binding drift")
            snapshot_id = str(snapshot.get("snapshotId") or "")
            if not snapshot_id or snapshot_id in snapshot_ids:
                raise ValueError("Phase 3 anchors must use distinct PredictionSnapshots")
            snapshot_ids.add(snapshot_id)
            if "label" not in frame:
                raise ValueError(f"anchor {anchor.anchor_id} PredictionSnapshot must embed labels")
            frames.append(frame)
            runs.append(
                {
                    "path": str(run_path),
                    "sha256": sha256_file(run_path),
                    "sourceCodeCommit": source_commit,
                    "predictionSnapshot": {
                        "path": str(snapshot_path),
                        "sha256": sha256_file(snapshot_path),
                        "snapshotId": snapshot_id,
                        "payloadSha256": snapshot["payload"]["sha256"],
                    },
                }
            )
        combined = pd.concat(frames).sort_index()
        if combined.index.has_duplicates:
            raise ValueError(f"anchor {anchor.anchor_id} PredictionSnapshots overlap")
        if reference_index is None:
            reference_index = combined.index
            reference_label = pd.to_numeric(combined["label"], errors="coerce")
        else:
            if not combined.index.equals(reference_index):
                raise ValueError("Phase 3 anchor PredictionSnapshot keys do not align")
            assert reference_label is not None
            try:
                pd.testing.assert_series_equal(
                    pd.to_numeric(combined["label"], errors="coerce"),
                    reference_label,
                    check_dtype=False,
                    check_names=False,
                )
            except AssertionError as exc:
                raise ValueError("Phase 3 anchor labels do not align") from exc
        result[anchor.anchor_id] = {
            "experimentId": anchor.experiment_id,
            "role": anchor.role,
            "featureSet": anchor.feature_set_id,
            "model": anchor.model,
            "runs": runs,
            "rows": len(combined),
            "startDate": str(combined.index.get_level_values("datetime").min().date()),
            "endDate": str(combined.index.get_level_values("datetime").max().date()),
        }
    if len(source_commits) != 1:
        raise ValueError("Phase 3 anchors were not produced by one source-code commit")
    return result


def load_stability_lock(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    lock = _load_json(source, "Phase 3 design lock")
    if lock.get("schemaVersion") != PHASE3_LOCK_SCHEMA:
        raise ValueError(f"unsupported Phase 3 design lock: {lock.get('schemaVersion')}")
    recorded = str(lock.get("lockSha256") or "")
    actual = sha256_json({key: value for key, value in lock.items() if key != "lockSha256"})
    if recorded != actual:
        raise ValueError("Phase 3 design lock checksum mismatch")
    entry = _mapping(lock.get("entryCondition"), "Phase 3 entry condition")
    if entry.get("phase2AcceptedCount") != 0 or entry.get("state") != "PHASE2_COMPLETE_REJECTED":
        raise ValueError("Phase 3 design lock does not preserve its rejected Phase 2 entry condition")
    for key in ("phase2Acceptance", "phase2CandidateMetrics", "phase2Evidence", "dataReleaseAcceptance"):
        provenance = _mapping(entry.get(key), f"Phase 3 entry condition {key}")
        if not str(provenance.get("sha256") or ""):
            raise ValueError(f"Phase 3 design lock is missing {key} provenance")
    isolation = _mapping(lock.get("isolation"), "Phase 3 isolation")
    if (
        isolation.get("finalHoldoutArtifactsAllowed") is not False
        or isolation.get("formalCandidatesAllowed") is not False
        or lock.get("publishingAuthorized") is not False
    ):
        raise ValueError("Phase 3 design lock does not seal candidate/holdout/publishing access")
    return lock


def write_stability_contract_lock(
    *,
    candidate_acceptance: str | Path,
    phase2_evidence: str | Path,
    candidate_data_acceptance: str | Path,
    contract_path: str | Path,
    output: str | Path,
) -> Path:
    acceptance_path = Path(candidate_acceptance).expanduser().resolve()
    evidence_path = Path(phase2_evidence).expanduser().resolve()
    data_acceptance_path = Path(candidate_data_acceptance).expanduser().resolve()
    contract_source = Path(contract_path).expanduser().resolve()
    contract = load_stability_contract(contract_source)
    acceptance = _validate_candidate_acceptance(acceptance_path, contract.predecessor_program)
    evidence = _load_json(evidence_path, "Phase 2 evidence index")
    if evidence.get("schemaVersion") != PHASE2_EVIDENCE_SCHEMA:
        raise ValueError(f"unsupported Phase 2 evidence index: {evidence.get('schemaVersion')}")
    if evidence.get("finalHoldout") is not False:
        raise ValueError("Phase 2 evidence index must set finalHoldout=false")
    candidate_metrics_binding = _validate_candidate_acceptance_provenance(
        acceptance_path, acceptance, evidence_path
    )
    contract_lock_sha = str(evidence.get("contractLockSha256") or "")
    if not contract_lock_sha or acceptance.get("contractLockSha256") != contract_lock_sha:
        raise ValueError("Phase 2 acceptance/evidence contract lock mismatch")

    base = evidence_path.parent
    release_path = _resolve(base, evidence.get("dataReleaseManifest"), "DataRelease manifest")
    release = _load_json(release_path, "DataRelease manifest")
    recorded_release_sha = str(release.get("manifestSha256") or "")
    if recorded_release_sha != sha256_json(
        {key: value for key, value in release.items() if key != "manifestSha256"}
    ):
        raise ValueError("DataRelease manifest checksum mismatch")
    data_release_id = str(release.get("dataReleaseId") or "")
    identity = {
        key: value
        for key, value in release.items()
        if key not in {"dataReleaseId", "identitySha256", "manifestSha256", "publishedAt"}
    }
    identity_sha256 = sha256_json(identity)
    if release.get("identitySha256") != identity_sha256 or data_release_id != f"ds_{identity_sha256}":
        raise ValueError("DataRelease identity mismatch")
    dataset_version_id = str(evidence.get("datasetVersionId") or "").strip()
    if not data_release_id or not dataset_version_id:
        raise ValueError("Phase 2 evidence is missing DataRelease or DatasetVersion identity")
    data_acceptance_binding = _validate_data_release_acceptance(
        data_acceptance_path,
        data_release_id=data_release_id,
        manifest_sha256=recorded_release_sha,
    )

    experiments = _mapping(evidence.get("ablationExperiments"), "Phase 2 ablation experiments")
    for anchor in contract.anchors:
        anchor_evidence = _mapping(experiments.get(anchor.experiment_id), f"anchor {anchor.anchor_id}")
        if anchor_evidence.get("portfolioManifest"):
            raise ValueError("Phase 3-D prohibits unbound optional portfolio evidence")

    feature_reference = _resolve(base, evidence.get("featureSnapshot"), "FeatureSnapshot")
    feature_manifest_path = (
        feature_reference / "manifest.json" if feature_reference.is_dir() else feature_reference
    )
    feature_manifest = _load_json(feature_manifest_path, "FeatureSnapshot manifest")
    feature_contract = _mapping(feature_manifest.get("contract"), "FeatureSnapshot contract")
    if (
        feature_contract.get("datasetId") != data_release_id
        or str(feature_contract.get("datasetVersionId") or "") != dataset_version_id
    ):
        raise ValueError("FeatureSnapshot does not match Phase 2 DataRelease/DatasetVersion")
    feature_snapshot_id = str(feature_manifest.get("featureSnapshotId") or "")
    if not feature_snapshot_id:
        raise ValueError("FeatureSnapshot identity is missing")
    feature_root = feature_manifest_path.parent
    for raw in _sequence(feature_manifest.get("files"), "FeatureSnapshot files"):
        item = _mapping(raw, "FeatureSnapshot file")
        partition = (feature_root / str(item.get("name") or "")).resolve()
        if (
            partition.parent != feature_root
            or not partition.is_file()
            or sha256_file(partition) != item.get("sha256")
        ):
            raise ValueError(f"FeatureSnapshot partition checksum mismatch: {partition.name}")
    expected_feature_snapshot_id = "fs_" + sha256_json(
        {
            "featureRecipeId": feature_manifest.get("featureRecipeId"),
            "coverage": feature_manifest.get("coverage"),
            "files": feature_manifest.get("files"),
        }
    )
    if feature_snapshot_id != expected_feature_snapshot_id:
        raise ValueError("FeatureSnapshot identity mismatch")
    labels_path = _resolve(base, evidence.get("labels"), "Phase 2 labels")

    regime_reference = Path(contract.regime_path).expanduser()
    regime_path = (
        regime_reference if regime_reference.is_absolute() else Path.cwd() / regime_reference
    ).resolve()
    regime = load_regime_spec(regime_path)
    if regime.minimum_sessions != contract.diagnostics.minimum_regime_sessions:
        raise ValueError("Phase 3 and regime minimum-session requirements differ")
    anchors = _anchor_lineage(
        evidence_path,
        evidence,
        contract.anchors,
        data_release_id=data_release_id,
        dataset_version_id=dataset_version_id,
        feature_snapshot_id=feature_snapshot_id,
        label_spec=contract.label_spec,
    )
    revision = git_revision(Path(__file__).resolve().parents[3])
    implementation_files = (
        "phase3_contract.py",
        "phase3_program.py",
        "phase3_diagnostics.py",
        "phase3_decay.py",
        "regime.py",
        "regime_diagnostics.py",
    )
    implementation_root = Path(__file__).resolve().parent
    implementation = {
        name: sha256_file(implementation_root / name)
        for name in implementation_files
        if (implementation_root / name).is_file()
    }
    payload: dict[str, Any] = {
        "schemaVersion": PHASE3_LOCK_SCHEMA,
        "programId": contract.program_id,
        "predecessorProgram": contract.predecessor_program,
        "entryCondition": {
            "state": "PHASE2_COMPLETE_REJECTED",
            "phase2AcceptedCount": 0,
            "phase2Acceptance": {
                "path": str(acceptance_path),
                "sha256": sha256_file(acceptance_path),
                "acceptanceSha256": acceptance["acceptanceSha256"],
            },
            "phase2CandidateMetrics": candidate_metrics_binding,
            "phase2Evidence": {
                "path": str(evidence_path),
                "sha256": sha256_file(evidence_path),
                "contractLockSha256": contract_lock_sha,
            },
            "dataReleaseAcceptance": data_acceptance_binding,
        },
        "contract": contract.to_manifest(),
        "lineage": {
            "dataRelease": {
                "path": str(release_path),
                "dataReleaseId": data_release_id,
                "manifestSha256": recorded_release_sha,
            },
            "datasetVersionId": dataset_version_id,
            "featureSnapshot": {
                "path": str(feature_manifest_path),
                "featureSnapshotId": feature_snapshot_id,
                "sha256": sha256_file(feature_manifest_path),
            },
            "labels": {"path": str(labels_path), "sha256": sha256_file(labels_path)},
            "anchors": anchors,
            "regimeSpec": {
                "path": str(regime_path),
                "fileSha256": regime.file_sha256,
                "semanticSha256": regime.semantic_sha256,
            },
            "sourceCodeCommit": revision.get("commit"),
            "sourceCodeDirty": revision.get("dirty"),
            "implementationSha256": implementation,
        },
        "state": "PHASE3_DESIGN_LOCKED",
        "researchWindow": "ROLLING_OOS_ONLY",
        "diagnosisOnly": True,
        "isolation": {
            "finalHoldoutArtifactsAllowed": False,
            "formalCandidatesAllowed": False,
            "phase2OverlaysUnlocked": False,
        },
        "publishingAuthorized": False,
    }
    payload["lockSha256"] = sha256_json(payload)
    target = Path(output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        existing = _load_json(target, "existing Phase 3 design lock")
        if existing != payload:
            raise ValueError("existing Phase 3 design lock differs")
        return target
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return target
