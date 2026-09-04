from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any, Mapping

import pandas as pd

from ..lineage import git_revision, sha256_json
from ..settings import Settings
from ..store import sha256_file
from .attribution_study import ATTRIBUTION_STUDY_SCHEMA
from .explanation_study import EXPLANATION_STUDY_SCHEMA
from .phase1_synthesis import (
    derive_feature_evidence,
    derive_phase1_recommendation,
    derive_regime_evidence,
    load_phase1_synthesis_spec,
)
from .regime import REQUIRED_DIMENSIONS
from .regime_study import REGIME_STUDY_SCHEMA
from .study import STUDY_SCHEMA, _mapping


SYNTHESIS_STUDY_SCHEMA = "alpha_phase1_synthesis_v1"
SYNTHESIS_MANIFEST_NAME = "alpha_phase_1_manifest.json"
SOURCE_STUDY_TYPES = {
    STUDY_SCHEMA: "ALPHA_RESEARCH_PHASE1_FEATURE_DIAGNOSTICS",
    REGIME_STUDY_SCHEMA: "ALPHA_RESEARCH_PHASE1_CAUSAL_REGIME_DIAGNOSTICS",
    ATTRIBUTION_STUDY_SCHEMA: "ALPHA_RESEARCH_PHASE1_PREDICTION_TO_PORTFOLIO_FAILURE_ATTRIBUTION",
    EXPLANATION_STUDY_SCHEMA: "ALPHA_RESEARCH_PHASE1_MODEL_EXPLANATION",
}


@dataclass(frozen=True)
class SourceStudy:
    name: str
    path: Path
    manifest: Mapping[str, Any]
    artifacts: Mapping[str, Path]

    @property
    def study_id(self) -> str:
        return str(self.manifest.get("studyId") or "")

    @property
    def contract(self) -> Mapping[str, Any]:
        return _mapping(self.manifest.get("contract"), f"{self.name} contract")

    def artifact(self, name: str) -> Path:
        try:
            return self.artifacts[name]
        except KeyError as exc:
            raise ValueError(f"{self.name} is missing required artifact: {name}") from exc


def _load_json(path: Path, name: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"{name} is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{name} must be a JSON object")
    return payload


def _load_source(
    name: str,
    path: str | Path,
    *,
    schema: str,
    required_status: tuple[str, str],
) -> SourceStudy:
    resolved = Path(path).expanduser().resolve()
    manifest = _load_json(resolved, f"{name} manifest")
    if manifest.get("schemaVersion") != schema:
        raise ValueError(f"unsupported {name} schema")
    if manifest.get("studyType") != SOURCE_STUDY_TYPES[schema]:
        raise ValueError(f"unsupported {name} study type")
    if not str(manifest.get("studyId") or "").strip():
        raise ValueError(f"{name} studyId is missing")
    status = _mapping(manifest.get("status"), f"{name} status")
    if status.get("systemIntegrity") != "PASS":
        raise ValueError(f"{name} systemIntegrity must be PASS")
    if status.get(required_status[0]) != required_status[1]:
        raise ValueError(f"{name} status {required_status[0]} must be {required_status[1]}")
    if manifest.get("selectionUsesFinalHoldout") is not False:
        raise ValueError(f"{name} does not prove final-holdout isolation")
    if manifest.get("publishingAuthorized") is not False:
        raise ValueError(f"{name} unexpectedly authorizes publishing")
    contract = _mapping(manifest.get("contract"), f"{name} contract")
    if contract.get("selectionUsesFinalHoldout") is not False:
        raise ValueError(f"{name} contract does not prove final-holdout isolation")
    if contract.get("publishingAuthorized") is not False:
        raise ValueError(f"{name} contract unexpectedly authorizes publishing")
    root = resolved.parent.resolve()
    artifacts: dict[str, Path] = {}
    for raw in manifest.get("artifacts", []):
        artifact = _mapping(raw, f"{name} artifact")
        artifact_name = str(artifact.get("name") or "")
        relative = str(artifact.get("path") or "")
        if not artifact_name or artifact_name in artifacts or not relative:
            raise ValueError(f"{name} contains a missing or duplicate artifact name")
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise ValueError(f"{name} artifact escapes its immutable bundle: {target}")
        if sha256_file(target) != artifact.get("sha256"):
            raise ValueError(f"{name} artifact checksum mismatch: {target}")
        artifacts[artifact_name] = target
    if not artifacts:
        raise ValueError(f"{name} contains no artifacts")
    return SourceStudy(name=name, path=resolved, manifest=manifest, artifacts=artifacts)


def _assert_equal(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise ValueError(f"{name} mismatch: {actual!r} != {expected!r}")


def _required_float(value: object, name: str) -> float:
    if value is None or isinstance(value, (dict, list, tuple)):
        raise ValueError(f"{name} must be numeric")
    try:
        return float(str(value))
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc


def _validate_identity_and_chain(
    feature: SourceStudy,
    regime: SourceStudy,
    attribution: SourceStudy,
    explanation: SourceStudy,
) -> dict[str, object]:
    feature_contract = feature.contract
    regime_contract = regime.contract
    attribution_contract = attribution.contract
    explanation_contract = explanation.contract
    _assert_equal(
        regime_contract.get("baseStudyManifestSha256"),
        sha256_file(feature.path),
        "regime→feature manifest",
    )
    _assert_equal(
        attribution_contract.get("regimeStudyManifestSha256"),
        sha256_file(regime.path),
        "attribution→regime manifest",
    )
    for source_name, expected_path in (
        ("baseStudyManifestSha256", feature.path),
        ("regimeStudyManifestSha256", regime.path),
        ("attributionStudyManifestSha256", attribution.path),
    ):
        _assert_equal(
            explanation_contract.get(source_name),
            sha256_file(expected_path),
            f"explanation {source_name}",
        )
    identity_fields = (
        "dataReleaseId",
        "datasetVersionId",
        "featureSnapshotId",
        "featureSnapshotManifestSha256",
        "alphaPackId",
        "alphaPackSha256",
        "labelSpecId",
        "labelSpec",
        "splitSpecSha256",
        "taxonomyId",
        "taxonomySha256",
    )
    for field in identity_fields:
        expected = feature_contract.get(field)
        if expected is None or expected == "" or (isinstance(expected, Mapping) and not expected):
            raise ValueError(f"feature {field} identity is missing")
        _assert_equal(regime_contract.get(field), expected, f"regime {field}")
        _assert_equal(explanation_contract.get(field), expected, f"explanation {field}")
    for field in ("dataReleaseId", "featureSnapshotId", "splitSpecSha256"):
        _assert_equal(attribution_contract.get(field), feature_contract.get(field), f"attribution {field}")
    attribution_label = _mapping(attribution_contract.get("labelSpec"), "attribution LabelSpec")
    _assert_equal(
        attribution_label.get("id"), feature_contract.get("labelSpecId"), "attribution LabelSpec id"
    )
    _assert_equal(
        attribution_label.get("contract"),
        feature_contract.get("labelSpec"),
        "attribution LabelSpec contract",
    )
    acceptance_sha = _mapping(feature_contract.get("fullWalkForwardAcceptance"), "feature acceptance").get(
        "sha256"
    )
    if not str(acceptance_sha or ""):
        raise ValueError("feature acceptance identity is missing")
    for name, contract in (
        ("regime", regime_contract),
        ("attribution", attribution_contract),
        ("explanation", explanation_contract),
    ):
        _assert_equal(
            contract.get("fullWalkForwardAcceptanceSha256"),
            acceptance_sha,
            f"{name} acceptance",
        )
    regime_predictions = dict(_mapping(regime_contract.get("modelPredictionSha256"), "regime predictions"))
    if set(regime_predictions) != {"ridge", "lightgbm", "xgboost"}:
        raise ValueError("regime prediction identity must contain exactly Ridge, LightGBM, and XGBoost")
    if any(not str(value or "") for value in regime_predictions.values()):
        raise ValueError("regime prediction identity contains an empty checksum")
    _assert_equal(
        dict(_mapping(attribution_contract.get("modelPredictionSha256"), "attribution predictions")),
        regime_predictions,
        "attribution predictions",
    )
    _assert_equal(
        dict(_mapping(explanation_contract.get("modelPredictionSha256"), "explanation predictions")),
        regime_predictions,
        "explanation predictions",
    )
    _assert_equal(
        _mapping(feature_contract.get("fullWalkForwardAcceptance"), "feature acceptance").get(
            "xgboostPredictionSha256"
        ),
        regime_predictions["xgboost"],
        "feature XGBoost prediction",
    )
    if explanation.manifest.get("modelArtifactCertification") != "DERIVED_SAME_RECORDER_ADDITIVITY":
        raise ValueError("model explanation artifact certification is unsupported")
    _assert_equal(
        explanation_contract.get("modelArtifactCertification"),
        "DERIVED_SAME_RECORDER_ADDITIVITY",
        "explanation model artifact certification",
    )
    fold_inputs = _mapping(explanation_contract.get("foldModelInputs"), "fold model inputs")
    if set(fold_inputs) != {"ridge", "lightgbm", "xgboost"}:
        raise ValueError("model explanation fold inputs are incomplete")
    tolerance = _required_float(
        _mapping(explanation_contract.get("explanationSpec"), "explanation spec").get(
            "shapAdditivityTolerance"
        ),
        "SHAP additivity tolerance",
    )
    for model, raw_folds in fold_inputs.items():
        folds = _mapping(raw_folds, f"{model} fold inputs")
        if not folds:
            raise ValueError(f"{model} explanation contains no rolling folds")
        for fold, raw in folds.items():
            item = _mapping(raw, f"{model} {fold} input")
            if not str(item.get("recorderModelSha256") or "") or not str(
                item.get("processorStateSha256") or ""
            ):
                raise ValueError(f"{model} {fold} model/processor binding is missing")
            if model != "ridge":
                error = _required_float(
                    item.get("shapAdditivityMaxAbsError"), f"{model} {fold} SHAP additivity error"
                )
                if error > tolerance:
                    raise ValueError(f"{model} {fold} SHAP additivity exceeds its contract")
    return {
        "dataReleaseId": feature_contract.get("dataReleaseId"),
        "datasetVersionId": feature_contract.get("datasetVersionId"),
        "featureSnapshotId": feature_contract.get("featureSnapshotId"),
        "featureSnapshotManifestSha256": feature_contract.get("featureSnapshotManifestSha256"),
        "alphaPackId": feature_contract.get("alphaPackId"),
        "alphaPackSha256": feature_contract.get("alphaPackSha256"),
        "labelSpecId": feature_contract.get("labelSpecId"),
        "labelSpecSha256": sha256_json(feature_contract.get("labelSpec")),
        "splitSpecSha256": feature_contract.get("splitSpecSha256"),
        "fullWalkForwardAcceptanceSha256": acceptance_sha,
        "taxonomyId": feature_contract.get("taxonomyId"),
        "taxonomySha256": feature_contract.get("taxonomySha256"),
        "modelPredictionSha256": regime_predictions,
    }


def _validate_regime_availability(regime: SourceStudy) -> tuple[str, list[dict[str, object]]]:
    status = str(_mapping(regime.manifest.get("status"), "regime status").get("regimeDiagnostics"))
    availability = _mapping(regime.manifest.get("availability"), "regime availability")
    if set(availability) != set(REQUIRED_DIMENSIONS):
        raise ValueError("regime availability must contain all five predeclared dimensions")
    statuses = {
        name: _mapping(availability.get(name), f"regime availability {name}").get("status")
        for name in REQUIRED_DIMENSIONS
    }
    if any(value not in {"AVAILABLE", "INPUT_UNAVAILABLE"} for value in statuses.values()):
        raise ValueError("regime availability status must be AVAILABLE or INPUT_UNAVAILABLE")
    missing = [name for name in REQUIRED_DIMENSIONS if statuses[name] != "AVAILABLE"]
    if status == "PASS" and missing:
        raise ValueError("PASS regime diagnostics contain unavailable dimensions")
    if status == "PARTIAL" and missing != ["industry_breadth"]:
        raise ValueError("only PIT industry breadth may be unavailable in a non-blocking PARTIAL study")
    if status not in {"PASS", "PARTIAL"}:
        raise ValueError("regime diagnostics must be PASS or PARTIAL")
    gaps = (
        [
            {
                "gapId": "DATA_GAP_PIT_INDUSTRY",
                "component": "industry_breadth",
                "status": "INPUT_UNAVAILABLE",
                "blocking": False,
                "treatment": "excluded from evidence denominators; not negative evidence",
            }
        ]
        if missing
        else []
    )
    return status, gaps


def _artifact_index(sources: Mapping[str, SourceStudy]) -> list[dict[str, object]]:
    purposes = {
        "feature_summary.parquet": "feature stability and stable-signal evidence",
        "feature_clusters.json": "within-family redundancy evidence",
        "model_regime_diagnostics.parquet": "sample-qualified conditional model evidence",
        "regime_labels.parquet": "causal regime availability and labeling",
        "failure_attribution_summary.json": "Signal→Ranking→Portfolio→Cost loss classification",
        "model_explanation_summary.json": "H1/H2/H3 and XGBoost mechanism evidence",
    }
    rows: list[dict[str, object]] = []
    for source_name, source in sorted(sources.items()):
        for artifact_name, artifact_path in sorted(source.artifacts.items()):
            rows.append(
                {
                    "logicalName": f"{source_name}:{artifact_name}",
                    "sourceStudy": source_name,
                    "sourceStudyId": source.study_id,
                    "sourceArtifact": artifact_name,
                    "sourceSha256": sha256_file(artifact_path),
                    "bundlePath": f"evidence/{source_name}/{artifact_name}",
                    "purpose": purposes.get(artifact_name, "supporting Phase 1 evidence"),
                }
            )
    return rows


def _source_contract(source: SourceStudy) -> dict[str, object]:
    return {
        "schemaVersion": source.manifest.get("schemaVersion"),
        "studyId": source.study_id,
        "manifestSha256": sha256_file(source.path),
        "artifacts": {name: sha256_file(path) for name, path in sorted(source.artifacts.items())},
    }


def _bundle_relative_path(root: PurePath, path: PurePath) -> str:
    return path.relative_to(root).as_posix()


def _artifact_entry(root: Path, path: Path) -> dict[str, object]:
    return {
        "name": path.name,
        "path": _bundle_relative_path(root, path),
        "sha256": sha256_file(path),
    }


def _expected_artifact_paths(contract: Mapping[str, Any]) -> set[str]:
    source_manifests = _mapping(contract.get("sourceManifests"), "source manifests")
    expected = {
        "phase1_artifact_index.json",
        "phase1_evidence_summary.json",
        "alpha_phase_1_report.md",
    }
    for source_name, raw_source in source_manifests.items():
        source = _mapping(raw_source, f"source manifest {source_name}")
        artifacts = _mapping(source.get("artifacts"), f"source artifacts {source_name}")
        expected.add(f"source_{source_name}_manifest.json")
        expected.update(f"evidence/{source_name}/{name}" for name in artifacts)
    return expected


def _validate_existing(
    path: Path,
    contract: Mapping[str, Any],
    *,
    result: Mapping[str, Any],
    status: Mapping[str, Any],
    gaps: list[dict[str, object]],
) -> Path:
    manifest_path = path / SYNTHESIS_MANIFEST_NAME
    manifest = _load_json(manifest_path, "existing Phase 1 synthesis manifest")
    if manifest.get("schemaVersion") != SYNTHESIS_STUDY_SCHEMA or manifest.get("studyId") != path.name:
        raise ValueError(f"existing Phase 1 synthesis identity differs: {path}")
    if manifest.get("contract") != dict(contract):
        raise ValueError(f"existing Phase 1 synthesis contract differs: {path}")
    _assert_equal(manifest.get("status"), dict(status), "existing synthesis status")
    _assert_equal(
        manifest.get("primaryRecommendation"),
        result.get("primaryRecommendation"),
        "existing primary recommendation",
    )
    _assert_equal(
        manifest.get("recommendationRuleId"),
        result.get("recommendationRuleId"),
        "existing recommendation rule",
    )
    _assert_equal(
        manifest.get("candidateAssessment"),
        result.get("candidateAssessment"),
        "existing candidate assessment",
    )
    if manifest.get("selectionUsesFinalHoldout") is not False:
        raise ValueError("existing synthesis does not prove final-holdout isolation")
    if manifest.get("publishingAuthorized") is not False:
        raise ValueError("existing synthesis unexpectedly authorizes publishing")
    expected_posture = {
        "industryBreadth": "INPUT_UNAVAILABLE" if gaps else "AVAILABLE",
        "modelArtifactCertification": "DERIVED_SAME_RECORDER_ADDITIVITY",
    }
    _assert_equal(manifest.get("evidencePosture"), expected_posture, "existing evidence posture")
    root = path.resolve()
    artifact_paths: set[str] = set()
    for raw in manifest.get("artifacts", []):
        artifact = _mapping(raw, "Phase 1 synthesis artifact")
        relative = str(artifact.get("path") or "")
        if not relative or relative in artifact_paths:
            raise ValueError("existing Phase 1 synthesis has a missing or duplicate artifact path")
        artifact_paths.add(relative)
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise ValueError(f"Phase 1 synthesis artifact is missing or escapes the bundle: {target}")
        if sha256_file(target) != artifact.get("sha256"):
            raise ValueError(f"Phase 1 synthesis artifact checksum mismatch: {target}")
    if artifact_paths != _expected_artifact_paths(contract):
        raise ValueError("existing Phase 1 synthesis artifact inventory differs from its source contract")
    return manifest_path


def _write_report(
    path: Path,
    *,
    study_id: str,
    result: Mapping[str, Any],
    status: Mapping[str, Any],
    gaps: list[dict[str, object]],
) -> None:
    feature = _mapping(_mapping(result.get("evidence"), "evidence").get("feature"), "feature evidence")
    regime = _mapping(_mapping(result.get("evidence"), "evidence").get("regime"), "regime evidence")
    explanation = _mapping(
        _mapping(result.get("evidence"), "evidence").get("modelExplanation"), "model explanation"
    )
    lines = [
        "# Alpha Research Phase 1 — Synthesis",
        "",
        f"- Study ID: `{study_id}`",
        f"- Phase 1 Completion: {status['phase1Completion']}",
        f"- Evidence Completeness: {status['evidenceCompleteness']}",
        f"- Regime Diagnostics: {status['regimeDiagnostics']}",
        f"- Decision: **{result['primaryRecommendation']}**",
        f"- Recommendation rule: `{result['recommendationRuleId']}`",
        "- Selection Uses Final Holdout: false",
        "- Publishing Authorized: false",
        "",
        "## Objective answers",
        "",
        f"1. Stable features: {feature.get('stableFeatureCount')} predeclared-direction features clear the stability gate; dilution evidence is {feature.get('redundancyOrUnstableFeatureDilution')}.",
        f"2. XGBoost mechanism: {explanation.get('xgbPrimaryMechanism')}; bounded tuning evidence is {explanation.get('boundedSensitivity')}.",
        f"3. Primary Alpha loss source: {_mapping(result.get('evidence'), 'evidence').get('primaryAlphaLossSource')}; repeatable conditional regime states: {regime.get('repeatableConditionalStateCount')}.",
        "",
        "## Candidate assessment",
        "",
        "| Recommendation | Eligible | Rule | Rejection / gap |",
        "| --- | --- | --- | --- |",
    ]
    for candidate in result["candidateAssessment"]:
        rejection = list(candidate["rejectionReasons"]) + list(candidate["gaps"])
        lines.append(
            f"| {candidate['recommendation']} | {str(candidate['eligible']).lower()} | "
            f"{candidate['ruleId']} | {'; '.join(rejection) if rejection else '—'} |"
        )
    lines.extend(["", "## Known evidence gaps", ""])
    if gaps:
        for gap in gaps:
            lines.append(f"- `{gap['gapId']}`: {gap['component']} = {gap['status']} (non-blocking).")
    else:
        lines.append("- None.")
    caveat = (
        "The industry gap is excluded from evidence denominators and is not treated as zero "
        "performance or negative evidence. "
        if gaps
        else ""
    )
    caveat += (
        "The model binary provenance remains derived through same-recorder prediction parity and "
        "SHAP additivity, not direct Full Acceptance binary certification."
    )
    lines.extend(["", caveat, "", f"Primary recommendation: {result['primaryRecommendation']}", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def _materialize_bundle(
    output_root: Path,
    *,
    contract: dict[str, Any],
    sources: Mapping[str, SourceStudy],
    result: Mapping[str, Any],
    status: Mapping[str, Any],
    gaps: list[dict[str, object]],
) -> Path:
    study_id = "aps_" + sha256_json(contract)
    output_root.mkdir(parents=True, exist_ok=True)
    target = output_root / study_id
    if target.exists():
        return _validate_existing(target, contract, result=result, status=status, gaps=gaps)
    building = Path(tempfile.mkdtemp(prefix=f".{study_id}.", dir=output_root))
    try:
        artifact_paths: list[Path] = []
        for source_name, source in sorted(sources.items()):
            receipt = building / f"source_{source_name}_manifest.json"
            shutil.copy2(source.path, receipt)
            artifact_paths.append(receipt)
            evidence_root = building / "evidence" / source_name
            evidence_root.mkdir(parents=True)
            for artifact_name, source_path in sorted(source.artifacts.items()):
                target_path = evidence_root / artifact_name
                shutil.copy2(source_path, target_path)
                artifact_paths.append(target_path)
        index_path = building / "phase1_artifact_index.json"
        index_path.write_text(
            json.dumps(_artifact_index(sources), ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        artifact_paths.append(index_path)
        evidence_summary = {
            "primaryRecommendation": result["primaryRecommendation"],
            "recommendationRuleId": result["recommendationRuleId"],
            "candidateAssessment": result["candidateAssessment"],
            "decisionEvidence": result["decisionEvidence"],
            "evidence": result["evidence"],
            "knownDataGaps": gaps,
        }
        summary_path = building / "phase1_evidence_summary.json"
        summary_path.write_text(
            json.dumps(evidence_summary, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        artifact_paths.append(summary_path)
        report_path = building / "alpha_phase_1_report.md"
        _write_report(report_path, study_id=study_id, result=result, status=status, gaps=gaps)
        artifact_paths.append(report_path)
        artifacts = [_artifact_entry(building, path) for path in artifact_paths]
        manifest = {
            "schemaVersion": SYNTHESIS_STUDY_SCHEMA,
            "studyId": study_id,
            "studyType": "ALPHA_RESEARCH_PHASE1_SYNTHESIS",
            "contract": contract,
            "status": dict(status),
            "primaryRecommendation": result["primaryRecommendation"],
            "recommendationRuleId": result["recommendationRuleId"],
            "candidateAssessment": result["candidateAssessment"],
            "decisionEvidence": result["decisionEvidence"],
            "evidencePosture": {
                "industryBreadth": "INPUT_UNAVAILABLE" if gaps else "AVAILABLE",
                "modelArtifactCertification": "DERIVED_SAME_RECORDER_ADDITIVITY",
            },
            "executionIsolation": {
                "modelTrainCalls": 0,
                "newPredictionArtifacts": 0,
                "featureMaterializationCalls": 0,
                "portfolioBacktestCalls": 0,
            },
            "selectionUsesFinalHoldout": False,
            "publishingAuthorized": False,
            "artifacts": artifacts,
        }
        manifest_path = building / SYNTHESIS_MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
        )
        try:
            os.replace(building, target)
        except OSError:
            if target.exists():
                return _validate_existing(target, contract, result=result, status=status, gaps=gaps)
            raise
        return target / SYNTHESIS_MANIFEST_NAME
    finally:
        if building.exists():
            shutil.rmtree(building, ignore_errors=True)


def run_phase1_synthesis(
    settings: Settings,
    *,
    feature_study: str | Path,
    regime_study: str | Path,
    attribution_study: str | Path,
    explanation_study: str | Path,
    synthesis_path: str | Path,
    output_root: str | Path | None = None,
) -> Path:
    sources = {
        "feature": _load_source(
            "feature",
            feature_study,
            schema=STUDY_SCHEMA,
            required_status=("featureDiagnostics", "PASS"),
        ),
        "regime": _load_source(
            "regime",
            regime_study,
            schema=REGIME_STUDY_SCHEMA,
            required_status=("systemIntegrity", "PASS"),
        ),
        "attribution": _load_source(
            "attribution",
            attribution_study,
            schema=ATTRIBUTION_STUDY_SCHEMA,
            required_status=("failureAttribution", "PASS"),
        ),
        "explanation": _load_source(
            "explanation",
            explanation_study,
            schema=EXPLANATION_STUDY_SCHEMA,
            required_status=("modelExplanation", "PASS"),
        ),
    }
    identity = _validate_identity_and_chain(
        sources["feature"], sources["regime"], sources["attribution"], sources["explanation"]
    )
    regime_status, gaps = _validate_regime_availability(sources["regime"])
    explanation_status = _mapping(sources["explanation"].manifest.get("status"), "explanation status")
    _assert_equal(explanation_status.get("regimeConditioning"), regime_status, "regime conditioning")
    spec = load_phase1_synthesis_spec(synthesis_path)
    feature_summary = pd.read_parquet(sources["feature"].artifact("feature_summary.parquet"))
    clusters = _load_json(sources["feature"].artifact("feature_clusters.json"), "feature clusters")
    model_regime = pd.read_parquet(sources["regime"].artifact("model_regime_diagnostics.parquet"))
    failure_summary = _load_json(
        sources["attribution"].artifact("failure_attribution_summary.json"),
        "failure attribution summary",
    )
    explanation_summary = _load_json(
        sources["explanation"].artifact("model_explanation_summary.json"),
        "model explanation summary",
    )
    _assert_equal(
        sources["attribution"].manifest.get("primaryAlphaLossSource"),
        failure_summary.get("primaryAlphaLossSource"),
        "attribution primary loss source",
    )
    _assert_equal(
        sources["explanation"].manifest.get("primaryMechanism"),
        explanation_summary.get("xgbPrimaryMechanism"),
        "explanation primary mechanism",
    )
    _assert_equal(
        explanation_status.get("boundedSensitivity"),
        explanation_summary.get("boundedSensitivity"),
        "explanation bounded sensitivity",
    )
    feature_evidence = derive_feature_evidence(feature_summary, clusters, spec=spec)
    regime_evidence = derive_regime_evidence(model_regime, spec=spec)
    result = derive_phase1_recommendation(
        failure_summary=failure_summary,
        explanation_summary=explanation_summary,
        feature_evidence=feature_evidence,
        regime_evidence=regime_evidence,
        spec=spec,
    )
    revision = git_revision(Path(__file__).resolve().parents[3])
    contract = {
        "schemaVersion": SYNTHESIS_STUDY_SCHEMA,
        "identity": identity,
        "sourceManifests": {name: _source_contract(source) for name, source in sorted(sources.items())},
        "synthesisSpec": spec.to_manifest(),
        "studyImplementationSha256": {
            name: sha256_file(Path(__file__).resolve().parent / name)
            for name in ("phase1_synthesis.py", "synthesis_study.py")
        },
        "studyCodeCommit": revision.get("commit"),
        "studyCodeDirty": revision.get("dirty"),
        "selectionUsesFinalHoldout": False,
        "publishingAuthorized": False,
    }
    status = {
        "systemIntegrity": "PASS",
        "featureDiagnostics": "PASS",
        "regimeDiagnostics": regime_status,
        "failureAttribution": "PASS",
        "modelExplanation": "PASS",
        "evidenceCompleteness": "PARTIAL" if gaps else "FULL",
        "phase1Synthesis": "PASS",
        "phase1Completion": "COMPLETE_WITH_KNOWN_DATA_GAP" if gaps else "COMPLETE",
    }
    destination = (
        Path(output_root).expanduser().resolve()
        if output_root is not None
        else settings.paths.output / "research" / "alpha_phase1" / "synthesis"
    )
    return _materialize_bundle(
        destination,
        contract=contract,
        sources=sources,
        result=result,
        status=status,
        gaps=gaps,
    )
