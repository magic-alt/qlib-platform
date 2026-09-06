from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from qlib_platform.research.studies import explanation


def _index() -> pd.MultiIndex:
    return pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-09-01"), "SZ000001"), (pd.Timestamp("2026-09-02"), "SZ000001")],
        names=["datetime", "instrument"],
    )


def test_json_default_and_prediction_normalization() -> None:
    assert explanation._json_default(np.int64(3)) == 3
    with pytest.raises(TypeError, match="not JSON serializable"):
        explanation._json_default(object())

    series = pd.Series([0.1, 0.2], index=_index())
    normalized = explanation._normalize_prediction(series, "series")
    assert normalized.columns.tolist() == ["score"]
    frame = pd.DataFrame({"prediction": [0.3, 0.4]}, index=_index())
    normalized_frame = explanation._normalize_prediction(frame, "frame")
    assert normalized_frame["score"].tolist() == [0.3, 0.4]
    with pytest.raises(ValueError, match="pandas"):
        explanation._normalize_prediction([1, 2], "bad")
    with pytest.raises(ValueError, match="MultiIndex"):
        explanation._normalize_prediction(pd.DataFrame({"score": [1]}), "bad")
    with pytest.raises(ValueError, match="one score"):
        explanation._normalize_prediction(
            pd.DataFrame({"a": [1, 2], "b": [3, 4]}, index=_index()), "bad"
        )


def test_prediction_equality_checks_keys_and_scores() -> None:
    expected = pd.DataFrame({"score": [1.0, 2.0]}, index=_index())
    explanation._assert_prediction_equal(expected.copy(), expected, "ok", 1e-12)
    other_index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-09-01"), "SH600000")], names=["datetime", "instrument"]
    )
    with pytest.raises(ValueError, match="keys differ"):
        explanation._assert_prediction_equal(
            pd.DataFrame({"score": [1.0]}, index=other_index), expected, "bad", 1e-12
        )
    changed = expected.copy()
    changed.iloc[1, 0] = 9.0
    with pytest.raises(ValueError, match="predictions differ"):
        explanation._assert_prediction_equal(changed, expected, "bad", 1e-12)


def test_stable_lock_and_fold_plan_validation() -> None:
    lock = {
        "dataRelease": "dr",
        "alphaPack": "alpha",
        "labelSpec": "label",
        "splitSpec": {
            "folds": [
                {"key": "rolling_01", "train": ["a", "b"], "test": ["c", "d"]},
                {"key": "final_holdout", "final_holdout": True},
            ]
        },
        "extra": "ignored",
    }
    stable = explanation._stable_lock(lock)
    assert stable["dataRelease"] == "dr"
    assert "extra" not in stable
    plan = explanation._rolling_fold_plan(lock)
    assert list(plan) == ["rolling_01"]
    with pytest.raises(ValueError, match="no fold plan"):
        explanation._rolling_fold_plan({"splitSpec": {"folds": None}})
    with pytest.raises(ValueError, match="invalid rolling fold"):
        explanation._rolling_fold_plan(
            {"splitSpec": {"folds": [{"key": "x"}, {"key": "x"}]}}
        )
    with pytest.raises(ValueError, match="no rolling folds"):
        explanation._rolling_fold_plan(
            {"splitSpec": {"folds": [{"key": "final_holdout", "final_holdout": True}]}}
        )


def test_resolve_recorder_artifacts(tmp_path) -> None:
    root = tmp_path / "mlruns"
    model = root / "experiment" / "run-1" / "artifacts" / "params.pkl"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"model")
    prediction = model.parent / "pred.pkl"
    prediction.write_bytes(b"prediction")
    resolved_model, resolved_prediction = explanation._resolve_recorder_artifacts("run-1", [root])
    assert resolved_model == model.resolve()
    assert resolved_prediction == prediction.resolve()
    with pytest.raises(FileNotFoundError, match="root"):
        explanation._resolve_recorder_artifacts("x", [tmp_path / "missing"])
    prediction.unlink()
    with pytest.raises(FileNotFoundError, match="prediction"):
        explanation._resolve_recorder_artifacts("run-1", [root])


def _shap_frame() -> pd.DataFrame:
    rows = []
    for fold in ("rolling_01", "rolling_02"):
        for feature, value in (("f1", 2.0), ("f2", 1.0)):
            rows.append(
                {
                    "model": "xgboost",
                    "scope_type": "FOLD",
                    "scope": fold,
                    "fold": fold,
                    "feature": feature,
                    "family": "price",
                    "role": "core",
                    "direction": "positive",
                    "observations": 10,
                    "sessions": 5,
                    "mean_shap": value / 10,
                    "mean_abs_shap": value,
                    "normalized_mean_abs_shap": value / 3,
                    "shap_std": value / 2,
                    "feature_shap_spearman": 0.5,
                    "additivity_max_abs_error": 1e-8,
                }
            )
    return pd.DataFrame(rows)


def test_shap_importance_and_interaction_aggregation() -> None:
    shap = _shap_frame()
    aggregate = explanation._aggregate_shap(
        shap, ["model", "feature"], "ALL_OOS", minimum_sessions=5
    )
    assert aggregate["rank"].tolist() == [1, 2]
    assert aggregate["sample_status"].eq("SUFFICIENT").all()
    insufficient = explanation._aggregate_shap(
        shap.iloc[:2], ["model", "feature"], "ALL_OOS", minimum_sessions=100
    )
    assert insufficient["sample_status"].eq("INSUFFICIENT_SAMPLE").all()
    assert explanation._aggregate_shap(pd.DataFrame(), ["model"], "ALL_OOS").empty

    importance = pd.DataFrame(
        [
            {
                "model": "ridge",
                "scope_type": "FOLD",
                "scope": "rolling_01",
                "fold": "rolling_01",
                "feature": feature,
                "family": "price",
                "role": "core",
                "direction": "positive",
                "importance_type": "coefficient",
                "raw_importance": value,
                "normalized_importance": value / 3,
                "sample_status": "AVAILABLE",
            }
            for feature, value in (("f1", 2.0), ("f2", 1.0))
        ]
    )
    importance_result = explanation._aggregate_importance(importance)
    assert len(importance_result) == 4
    assert set(importance_result["scope_type"]) == {"FOLD", "ALL_OOS"}

    interactions = pd.DataFrame(
        [
            {
                "scope_type": "FOLD",
                "scope": fold,
                "fold": fold,
                "feature_1": "f1",
                "feature_2": "f2",
                "family_1": "price",
                "family_2": "volume",
                "observations": 10,
                "sessions": 5,
                "mean_abs_pair_interaction": 0.2,
                "normalized_share": 0.4,
                "sample_status": "AVAILABLE",
                "rank": 1,
            }
            for fold in ("rolling_01", "rolling_02")
        ]
    )
    interaction_result = explanation._aggregate_interactions(interactions)
    all_oos = interaction_result.loc[interaction_result["scope_type"].eq("ALL_OOS")].iloc[0]
    assert all_oos["fold_presence_rate"] == pytest.approx(1.0)
    assert all_oos["rank"] == 1
    assert explanation._aggregate_interactions(pd.DataFrame()).empty


def test_artifact_and_existing_manifest_validation(tmp_path) -> None:
    artifact = tmp_path / "a.txt"
    artifact.write_text("a", encoding="utf-8")
    entry = explanation._artifact_entry(artifact, rows=1)
    assert entry["rows"] == 1
    manifest = {
        "contract": {"x": 1},
        "artifacts": [{"name": "a", "path": "a.txt", "sha256": entry["sha256"]}],
    }
    path = tmp_path / explanation.EXPLANATION_MANIFEST_NAME
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert explanation._validate_existing(tmp_path, {"x": 1}) == path
    with pytest.raises(ValueError, match="contract differs"):
        explanation._validate_existing(tmp_path, {"x": 2})


def test_validate_manifest_artifacts_detects_duplicates_and_checksum(tmp_path) -> None:
    artifact = tmp_path / "a.txt"
    artifact.write_text("a", encoding="utf-8")
    good = {
        "artifacts": [
            {
                "name": "a",
                "path": "a.txt",
                "sha256": explanation.sha256_file(artifact),
            }
        ]
    }
    explanation._validate_manifest_artifacts(tmp_path / "m.json", good, "study")
    duplicate = {"artifacts": good["artifacts"] * 2}
    with pytest.raises(ValueError, match="duplicate artifact name"):
        explanation._validate_manifest_artifacts(tmp_path / "m.json", duplicate, "study")
    bad = {"artifacts": [{"name": "a", "path": "a.txt", "sha256": "bad"}]}
    with pytest.raises(ValueError, match="checksum"):
        explanation._validate_manifest_artifacts(tmp_path / "m.json", bad, "study")


def test_materialize_bundle_is_content_addressed_and_reusable(tmp_path) -> None:
    contract = {"dataset": "ds", "explanationEvaluationCalls": 3}
    frames = {"importance.parquet": pd.DataFrame({"feature": ["f1"], "value": [1.0]})}
    summary = {"xgbPrimaryMechanism": "main_effects", "value": np.float64(1.5)}
    manifest_path = explanation._materialize_bundle(
        tmp_path,
        contract=contract,
        frames=frames,
        summary=summary,
        regime_status="PASS",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schemaVersion"] == explanation.EXPLANATION_STUDY_SCHEMA
    assert manifest["selectionUsesFinalHoldout"] is False
    assert manifest["executionIsolation"]["modelTrainCalls"] == 0
    assert explanation._materialize_bundle(
        tmp_path,
        contract=contract,
        frames=frames,
        summary=summary,
        regime_status="PASS",
    ) == manifest_path
    report = manifest_path.parent / "model_explanation_report.md"
    assert "Publishing Authorized: false" in report.read_text(encoding="utf-8")
