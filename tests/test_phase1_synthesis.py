from __future__ import annotations

import json
from pathlib import Path, PureWindowsPath
from typing import Any

import pandas as pd
import pytest

from qlib_platform.cli import parser
from qlib_platform.research.phase1_synthesis import (
    RECOMMENDATIONS,
    derive_feature_evidence,
    derive_phase1_recommendation,
    derive_regime_evidence,
    load_phase1_synthesis_spec,
)
from qlib_platform.research.synthesis_study import (
    _bundle_relative_path,
    _load_source,
    _validate_regime_availability,
    run_phase1_synthesis,
)
from qlib_platform.settings import Paths, Settings
from qlib_platform.store import sha256_file


def _spec():
    return load_phase1_synthesis_spec("configs/synthesis/ashare_phase1_synthesis_v1.yaml")


def _failure(loss_source: str) -> dict[str, object]:
    return {
        "primaryAlphaLossSource": loss_source,
        "signalLayer": {"status": "PASS"},
        "rankingLayer": {"status": "WEAK" if loss_source == "RANKING" else "PASS"},
        "portfolioLayer": {"status": "DRAG" if loss_source == "PORTFOLIO" else "PASS"},
        "costLayer": {"status": "PRIMARY" if loss_source == "COST" else "NOT_PRIMARY"},
    }


def _explanation(*, stable: bool = False, bounded: str = "NOT_RUN_NO_RETRAIN_AUTHORIZED"):
    return {
        "hypotheses": {
            "H1": {"status": "INCONCLUSIVE"},
            "H2": {"status": "INCONCLUSIVE"},
            "H3": {"status": "REJECTED" if stable else "INCONCLUSIVE"},
        },
        "stableSignalStructure": stable,
        "foldRelationshipStability": "STABLE" if stable else "INCONCLUSIVE",
        "regimeImportanceDrift": "INPUT_PARTIAL",
        "xgbPrimaryMechanism": "MAIN_EFFECT_NONLINEAR" if stable else "INCONCLUSIVE",
        "boundedSensitivity": bounded,
    }


def _feature_evidence(*, stable: int = 0, dilution: bool = False) -> dict[str, object]:
    return {
        "stableFeatureCount": stable,
        "redundancyOrUnstableFeatureDilution": dilution,
    }


def _regime_evidence(count: int = 0) -> dict[str, object]:
    return {"repeatableConditionalStateCount": count}


def test_repository_synthesis_config_freezes_priority_and_thresholds():
    spec = _spec()

    assert spec.recommendation_priority == RECOMMENDATIONS
    assert spec.minimum_oriented_rank_ic == pytest.approx(0.01)
    assert spec.minimum_regime_valid_folds == 2


def test_bundle_manifest_paths_are_posix_normalized_on_windows():
    root = PureWindowsPath(r"C:\research\aps_test")
    artifact = root / "evidence" / "feature" / "feature_summary.parquet"

    assert _bundle_relative_path(root, artifact) == "evidence/feature/feature_summary.parquet"


@pytest.mark.parametrize(
    ("failure", "explanation", "feature", "regime", "expected"),
    [
        (_failure("COST"), _explanation(), _feature_evidence(), _regime_evidence(), "PORTFOLIO_CONSTRUCTION"),
        (
            _failure("REGIME"),
            _explanation(),
            _feature_evidence(),
            _regime_evidence(2),
            "REGIME_AWARE_RESEARCH",
        ),
        (
            _failure("MODEL"),
            _explanation(stable=True, bounded="RECOVERABLE"),
            _feature_evidence(),
            _regime_evidence(),
            "XGBOOST_TUNING",
        ),
        (
            _failure("SIGNAL"),
            _explanation(),
            _feature_evidence(stable=2, dilution=True),
            _regime_evidence(),
            "ALPHA_PACK_V2",
        ),
        (_failure("SIGNAL"), _explanation(), _feature_evidence(), _regime_evidence(), "NO_GO_NEW_ALPHA"),
    ],
)
def test_recommendation_rules_cover_all_terminal_actions(
    failure: dict[str, object],
    explanation: dict[str, object],
    feature: dict[str, object],
    regime: dict[str, object],
    expected: str,
):
    result = derive_phase1_recommendation(
        failure_summary=failure,
        explanation_summary=explanation,
        feature_evidence=feature,
        regime_evidence=regime,
        spec=_spec(),
    )

    assert result["primaryRecommendation"] == expected
    assert sum(bool(item["eligible"]) for item in result["candidateAssessment"]) >= 1


def test_not_run_bounded_sensitivity_blocks_xgboost_tuning_and_priority_is_deterministic():
    result = derive_phase1_recommendation(
        failure_summary=_failure("MODEL"),
        explanation_summary=_explanation(stable=True),
        feature_evidence=_feature_evidence(stable=2, dilution=True),
        regime_evidence=_regime_evidence(),
        spec=_spec(),
    )
    tuning = next(
        item for item in result["candidateAssessment"] if item["recommendation"] == "XGBOOST_TUNING"
    )

    assert result["primaryRecommendation"] == "ALPHA_PACK_V2"
    assert tuning["eligible"] is False
    assert tuning["gaps"] == ["BOUNDED_SENSITIVITY_NOT_RECOVERABLE"]


def test_feature_and_regime_evidence_use_predeclared_sample_gates():
    feature_summary = pd.DataFrame(
        [
            {
                "feature": "VALUE",
                "role": "alpha",
                "orientation_available": True,
                "oriented_rank_ic_mean": 0.02,
                "positive_oriented_rank_ic_fold_ratio": 0.75,
                "rank_ic_hac_t": 2.2,
                "coverage_median": 0.95,
            },
            {
                "feature": "NOISE",
                "role": "alpha",
                "orientation_available": True,
                "oriented_rank_ic_mean": -0.01,
                "positive_oriented_rank_ic_fold_ratio": 0.25,
                "rank_ic_hac_t": -0.4,
                "coverage_median": 0.95,
            },
        ]
    )
    features = derive_feature_evidence(
        feature_summary,
        {"clusters": [{"members": ["VALUE", "NOISE"]}]},
        spec=_spec(),
    )
    model_regime = pd.DataFrame(
        [
            {
                "model": "xgboost",
                "dimension": "market_volatility",
                "state": "LOW_VOL",
                "sample_status": "SUFFICIENT",
                "rank_ic_mean": 0.03,
                "positive_rank_ic_fold_ratio": 0.75,
                "valid_folds": 3,
            },
            {
                "model": "xgboost",
                "dimension": "market_volatility",
                "state": "HIGH_VOL",
                "sample_status": "INSUFFICIENT",
                "rank_ic_mean": 0.08,
                "positive_rank_ic_fold_ratio": 1.0,
                "valid_folds": 4,
            },
        ]
    )
    regimes = derive_regime_evidence(model_regime, spec=_spec())

    assert features["stableFeatures"] == ["VALUE"]
    assert features["redundancyOrUnstableFeatureDilution"] is True
    assert regimes["repeatableConditionalStates"] == [
        {
            "dimension": "market_volatility",
            "state": "LOW_VOL",
            "rankIcMean": 0.03,
            "positiveFoldRatio": 0.75,
            "validFolds": 3,
        }
    ]


def _write_artifact(root: Path, name: str, value: Any) -> dict[str, object]:
    path = root / name
    if isinstance(value, pd.DataFrame):
        value.to_parquet(path, index=False)
    else:
        path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    return {"name": name, "path": name, "sha256": sha256_file(path)}


def _write_manifest(root: Path, name: str, payload: dict[str, object]) -> Path:
    path = root / name
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _source_chain(tmp_path: Path) -> tuple[Settings, dict[str, Path]]:
    identity = {
        "dataReleaseId": "release-test",
        "datasetVersionId": "dataset-test",
        "featureSnapshotId": "feature-test",
        "featureSnapshotManifestSha256": "feature-snapshot-sha",
        "alphaPackId": "alpha158_pit_v1",
        "alphaPackSha256": "alpha-pack-sha",
        "labelSpecId": "return_5d_t1_v1",
        "labelSpec": {"lookahead": 6, "delay": 1},
        "splitSpecSha256": "split-sha",
        "taxonomyId": "alpha158_pit_v1",
        "taxonomySha256": "taxonomy-sha",
    }
    acceptance_sha = "acceptance-sha"
    predictions = {"ridge": "ridge-sha", "lightgbm": "lgb-sha", "xgboost": "xgb-sha"}
    feature_root = tmp_path / "feature"
    feature_root.mkdir()
    feature_artifacts = [
        _write_artifact(
            feature_root,
            "feature_summary.parquet",
            pd.DataFrame(
                [
                    {
                        "feature": "VALUE",
                        "role": "alpha",
                        "orientation_available": True,
                        "oriented_rank_ic_mean": 0.02,
                        "positive_oriented_rank_ic_fold_ratio": 0.8,
                        "rank_ic_hac_t": 2.4,
                        "coverage_median": 0.99,
                    },
                    {
                        "feature": "NOISE",
                        "role": "alpha",
                        "orientation_available": True,
                        "oriented_rank_ic_mean": -0.01,
                        "positive_oriented_rank_ic_fold_ratio": 0.2,
                        "rank_ic_hac_t": -0.5,
                        "coverage_median": 0.99,
                    },
                ]
            ),
        ),
        _write_artifact(
            feature_root,
            "feature_clusters.json",
            {"clusters": [{"members": ["VALUE", "NOISE"]}]},
        ),
    ]
    feature_contract = {
        **identity,
        "fullWalkForwardAcceptance": {
            "sha256": acceptance_sha,
            "xgboostPredictionSha256": predictions["xgboost"],
        },
        "selectionUsesFinalHoldout": False,
        "publishingAuthorized": False,
    }
    feature_manifest = _write_manifest(
        feature_root,
        "alpha_research_study_manifest.json",
        {
            "schemaVersion": "alpha_research_study_v1",
            "studyId": "ars-test",
            "studyType": "ALPHA_RESEARCH_PHASE1_FEATURE_DIAGNOSTICS",
            "contract": feature_contract,
            "status": {"systemIntegrity": "PASS", "featureDiagnostics": "PASS"},
            "selectionUsesFinalHoldout": False,
            "publishingAuthorized": False,
            "artifacts": feature_artifacts,
        },
    )
    regime_root = tmp_path / "regime"
    regime_root.mkdir()
    model_regime = pd.DataFrame(
        [
            {
                "model": "xgboost",
                "dimension": "market_volatility",
                "state": "LOW_VOL",
                "sample_status": "SUFFICIENT",
                "rank_ic_mean": 0.01,
                "positive_rank_ic_fold_ratio": 0.5,
                "valid_folds": 2,
            }
        ]
    )
    regime_artifacts = [
        _write_artifact(regime_root, "model_regime_diagnostics.parquet", model_regime),
        _write_artifact(regime_root, "regime_labels.parquet", pd.DataFrame({"date": ["2026-01-01"]})),
    ]
    availability = {
        name: {"status": "INPUT_UNAVAILABLE" if name == "industry_breadth" else "AVAILABLE"}
        for name in (
            "market_trend",
            "market_volatility",
            "market_activity",
            "size_style",
            "industry_breadth",
        )
    }
    regime_contract = {
        **identity,
        "baseStudyManifestSha256": sha256_file(feature_manifest),
        "fullWalkForwardAcceptanceSha256": acceptance_sha,
        "modelPredictionSha256": predictions,
        "selectionUsesFinalHoldout": False,
        "publishingAuthorized": False,
    }
    regime_manifest = _write_manifest(
        regime_root,
        "regime_diagnostics_manifest.json",
        {
            "schemaVersion": "alpha_regime_study_v1",
            "studyId": "ard-test",
            "studyType": "ALPHA_RESEARCH_PHASE1_CAUSAL_REGIME_DIAGNOSTICS",
            "contract": regime_contract,
            "status": {"systemIntegrity": "PASS", "regimeDiagnostics": "PARTIAL"},
            "availability": availability,
            "selectionUsesFinalHoldout": False,
            "publishingAuthorized": False,
            "artifacts": regime_artifacts,
        },
    )
    attribution_root = tmp_path / "attribution"
    attribution_root.mkdir()
    failure = _failure("SIGNAL")
    attribution_artifacts = [_write_artifact(attribution_root, "failure_attribution_summary.json", failure)]
    attribution_contract = {
        "dataReleaseId": identity["dataReleaseId"],
        "featureSnapshotId": identity["featureSnapshotId"],
        "labelSpec": {"id": identity["labelSpecId"], "contract": identity["labelSpec"]},
        "splitSpecSha256": identity["splitSpecSha256"],
        "regimeStudyManifestSha256": sha256_file(regime_manifest),
        "fullWalkForwardAcceptanceSha256": acceptance_sha,
        "modelPredictionSha256": predictions,
        "selectionUsesFinalHoldout": False,
        "publishingAuthorized": False,
    }
    attribution_manifest = _write_manifest(
        attribution_root,
        "failure_attribution_manifest.json",
        {
            "schemaVersion": "alpha_failure_attribution_study_v1",
            "studyId": "afa-test",
            "studyType": "ALPHA_RESEARCH_PHASE1_PREDICTION_TO_PORTFOLIO_FAILURE_ATTRIBUTION",
            "contract": attribution_contract,
            "status": {"systemIntegrity": "PASS", "failureAttribution": "PASS"},
            "primaryAlphaLossSource": "SIGNAL",
            "selectionUsesFinalHoldout": False,
            "publishingAuthorized": False,
            "artifacts": attribution_artifacts,
        },
    )
    explanation_root = tmp_path / "explanation"
    explanation_root.mkdir()
    explanation_artifacts = [
        _write_artifact(explanation_root, "model_explanation_summary.json", _explanation())
    ]
    fold_inputs = {
        model: {
            "rolling_00": {
                "recorderModelSha256": f"{model}-model-sha",
                "processorStateSha256": "processor-sha",
                **({} if model == "ridge" else {"shapAdditivityMaxAbsError": 1e-8}),
            }
        }
        for model in ("ridge", "lightgbm", "xgboost")
    }
    explanation_contract = {
        **identity,
        "baseStudyManifestSha256": sha256_file(feature_manifest),
        "regimeStudyManifestSha256": sha256_file(regime_manifest),
        "attributionStudyManifestSha256": sha256_file(attribution_manifest),
        "fullWalkForwardAcceptanceSha256": acceptance_sha,
        "modelPredictionSha256": predictions,
        "modelArtifactCertification": "DERIVED_SAME_RECORDER_ADDITIVITY",
        "foldModelInputs": fold_inputs,
        "explanationSpec": {"shapAdditivityTolerance": 1e-5},
        "selectionUsesFinalHoldout": False,
        "publishingAuthorized": False,
    }
    explanation_manifest = _write_manifest(
        explanation_root,
        "model_explanation_manifest.json",
        {
            "schemaVersion": "alpha_model_explanation_study_v1",
            "studyId": "ame-test",
            "studyType": "ALPHA_RESEARCH_PHASE1_MODEL_EXPLANATION",
            "contract": explanation_contract,
            "status": {
                "systemIntegrity": "PASS",
                "modelExplanation": "PASS",
                "regimeConditioning": "PARTIAL",
                "boundedSensitivity": "NOT_RUN_NO_RETRAIN_AUTHORIZED",
            },
            "modelArtifactCertification": "DERIVED_SAME_RECORDER_ADDITIVITY",
            "primaryMechanism": "INCONCLUSIVE",
            "selectionUsesFinalHoldout": False,
            "publishingAuthorized": False,
            "artifacts": explanation_artifacts,
        },
    )
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={},
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )
    return settings, {
        "feature": feature_manifest,
        "regime": regime_manifest,
        "attribution": attribution_manifest,
        "explanation": explanation_manifest,
    }


def test_synthesis_builds_immutable_auditable_bundle_and_propagates_known_industry_gap(tmp_path: Path):
    settings, sources = _source_chain(tmp_path)
    kwargs = {
        "feature_study": sources["feature"],
        "regime_study": sources["regime"],
        "attribution_study": sources["attribution"],
        "explanation_study": sources["explanation"],
        "synthesis_path": "configs/synthesis/ashare_phase1_synthesis_v1.yaml",
        "output_root": tmp_path / "output",
    }

    first = run_phase1_synthesis(settings, **kwargs)
    second = run_phase1_synthesis(settings, **kwargs)
    manifest = json.loads(first.read_text(encoding="utf-8"))
    report = (first.parent / "alpha_phase_1_report.md").read_text(encoding="utf-8")

    assert second == first
    assert manifest["status"]["phase1Completion"] == "COMPLETE_WITH_KNOWN_DATA_GAP"
    assert manifest["status"]["evidenceCompleteness"] == "PARTIAL"
    assert manifest["primaryRecommendation"] == "ALPHA_PACK_V2"
    assert manifest["evidencePosture"] == {
        "industryBreadth": "INPUT_UNAVAILABLE",
        "modelArtifactCertification": "DERIVED_SAME_RECORDER_ADDITIVITY",
    }
    assert manifest["selectionUsesFinalHoldout"] is False
    assert manifest["publishingAuthorized"] is False
    assert report.count("Primary recommendation:") == 1
    assert (first.parent / "phase1_artifact_index.json").is_file()
    assert (first.parent / "source_feature_manifest.json").read_bytes() == sources["feature"].read_bytes()

    (first.parent / "phase1_evidence_summary.json").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum mismatch"):
        run_phase1_synthesis(settings, **kwargs)


def test_regime_partial_is_fail_closed_for_any_gap_other_than_pit_industry(tmp_path: Path):
    _, paths = _source_chain(tmp_path)
    payload = json.loads(paths["regime"].read_text(encoding="utf-8"))
    payload["availability"]["market_trend"]["status"] = "INPUT_UNAVAILABLE"
    paths["regime"].write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    regime = _load_source(
        "regime",
        paths["regime"],
        schema="alpha_regime_study_v1",
        required_status=("systemIntegrity", "PASS"),
    )

    with pytest.raises(ValueError, match="only PIT industry breadth"):
        _validate_regime_availability(regime)


def test_source_manifest_requires_explicit_holdout_and_publish_isolation(tmp_path: Path):
    _, paths = _source_chain(tmp_path)
    payload = json.loads(paths["feature"].read_text(encoding="utf-8"))
    payload.pop("selectionUsesFinalHoldout")
    paths["feature"].write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    with pytest.raises(ValueError, match="final-holdout isolation"):
        _load_source(
            "feature",
            paths["feature"],
            schema="alpha_research_study_v1",
            required_status=("featureDiagnostics", "PASS"),
        )


def test_phase1_synthesis_cli_requires_all_four_studies():
    args = parser().parse_args(
        [
            "phase1-synthesize",
            "--feature-study",
            "feature.json",
            "--regime-study",
            "regime.json",
            "--attribution-study",
            "attribution.json",
            "--explanation-study",
            "explanation.json",
        ]
    )

    assert args.command == "phase1-synthesize"
    assert args.synthesis == "configs/synthesis/ashare_phase1_synthesis_v1.yaml"
