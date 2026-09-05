from __future__ import annotations

import json

import pytest

from qlib_platform.lineage import sha256_json
from qlib_platform.research.workflow.hpo import (
    SearchSpace,
    StudySpec,
    _restricted_validation_dataset,
    run_optuna_study,
)


def test_study_spec_seals_holdout() -> None:
    with pytest.raises(ValueError, match="fixed"):
        StudySpec(
            name="invalid",
            dataset_version_id="ds_v1",
            feature_snapshot_id="fs_v1",
            model_family="lightgbm",
            model_profile_id="lightgbm_auto",
            model_profile_fingerprint="profile_fp",
            base_parameters_sha256=sha256_json({}),
            code_commit="abc123",
            selection_segments=("train", "valid", "final_holdout"),
        )


def test_optuna_study_persists_identity_trials_and_holdout_proof(tmp_path) -> None:
    search_space = SearchSpace.from_mapping(
        {
            "learning_rate": {"type": "float", "low": 0.01, "high": 0.2, "log": True},
            "num_leaves": {"type": "int", "low": 8, "high": 64},
        }
    )
    spec = StudySpec(
        name="lightgbm-validation-v1",
        dataset_version_id="ds_immutable_123",
        feature_snapshot_id="fs_immutable_456",
        model_family="lightgbm",
        model_profile_id="lightgbm_auto",
        model_profile_fingerprint="profile_fp",
        base_parameters_sha256=sha256_json({"loss": "mse"}),
        code_commit="deadbeef",
        objective_metric="rank_ic_mean",
        seed=7,
        n_trials=6,
    )

    def objective(context):
        lr = float(context.params["learning_rate"])
        leaves = int(context.params["num_leaves"])
        score = 0.1 - abs(lr - 0.05) - abs(leaves - 32) / 1000.0
        return {"rank_ic_mean": score, "ic_mean": score / 2.0}

    result = run_optuna_study(
        spec=spec,
        search_space=search_space,
        objective=objective,
        output_root=tmp_path,
        base_parameters={"loss": "mse"},
    )
    payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    assert payload["studyId"] == spec.study_id(search_space)
    assert payload["datasetVersionId"] == "ds_immutable_123"
    assert payload["featureSnapshotId"] == "fs_immutable_456"
    assert payload["codeCommit"] == "deadbeef"
    assert payload["modelProfileFingerprint"] == "profile_fp"
    assert payload["baseParameters"] == {"loss": "mse"}
    assert payload["selectionSegments"] == ["train", "valid"]
    assert payload["holdoutAccessAllowed"] is False
    assert payload["governance"]["finalHoldoutAccessed"] is False
    assert payload["governance"]["formalCandidateCreated"] is False
    assert len(payload["trials"]) == 6
    assert all(trial["holdoutAccessed"] is False for trial in payload["trials"])
    assert result.best_metrics["rank_ic_mean"] == pytest.approx(
        payload["bestTrial"]["metrics"]["rank_ic_mean"]
    )


def test_search_space_rejects_ambiguous_log_step() -> None:
    with pytest.raises(ValueError, match="cannot define step"):
        SearchSpace.from_mapping(
            {
                "learning_rate": {
                    "type": "float",
                    "low": 0.01,
                    "high": 0.1,
                    "log": True,
                    "step": 0.01,
                }
            }
        )


def test_restricted_validation_dataset_never_copies_source_test(monkeypatch) -> None:
    from types import SimpleNamespace

    class FakeDatasetH:
        def __init__(self, *, handler, segments):
            self.handler = handler
            self.segments = segments

    import qlib.data.dataset

    monkeypatch.setattr(qlib.data.dataset, "DatasetH", FakeDatasetH)
    source = SimpleNamespace(
        handler=object(),
        segments={
            "train": ("2020-01-01", "2022-12-31"),
            "valid": ("2023-01-01", "2023-12-31"),
            "test": ("2024-01-01", "2024-12-31"),
            "final_holdout": ("2025-01-01", "2025-12-31"),
        },
    )
    restricted = _restricted_validation_dataset(source)
    assert restricted.segments == {
        "train": source.segments["train"],
        "valid": source.segments["valid"],
        "test": source.segments["valid"],
    }
    assert source.segments["test"] not in restricted.segments.values()
    assert source.segments["final_holdout"] not in restricted.segments.values()


def test_study_rejects_base_parameter_fingerprint_drift(tmp_path) -> None:
    search_space = SearchSpace.from_mapping({"x": {"type": "int", "low": 1, "high": 2}})
    spec = StudySpec(
        name="identity-drift",
        dataset_version_id="ds",
        feature_snapshot_id="fs",
        model_family="lightgbm",
        model_profile_id="profile",
        model_profile_fingerprint="fp",
        base_parameters_sha256=sha256_json({"loss": "mse"}),
        code_commit="deadbeef",
        n_trials=1,
    )
    with pytest.raises(ValueError, match="base parameter fingerprint"):
        run_optuna_study(
            spec=spec,
            search_space=search_space,
            objective=lambda context: {"rank_ic_mean": float(context.params["x"])},
            output_root=tmp_path,
            base_parameters={"loss": "mae"},
        )
