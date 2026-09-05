from __future__ import annotations

import numpy as np
import pandas as pd

from qlib_platform.research.features import evaluation as evaluation_module
from qlib_platform.research.features.evaluation import (
    FactorEvaluationPolicy,
    evaluate_factors,
    write_factor_evaluation,
)
from qlib_platform.research.features.registry import FactorDefinition, FactorRegistry


def _panel() -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    rng = np.random.default_rng(7)
    dates = pd.date_range("2025-01-02", periods=40, freq="B")
    instruments = [f"S{i:03d}" for i in range(80)]
    index = pd.MultiIndex.from_product(
        [dates, instruments],
        names=["datetime", "instrument"],
    )
    latent = np.tile(np.linspace(-1.0, 1.0, len(instruments)), len(dates))
    stable_noise = rng.normal(0.0, 0.04, len(index))
    factor_a = latent + stable_noise
    factor_dup = factor_a + rng.normal(0.0, 0.005, len(index))
    factor_noise = rng.normal(0.0, 1.0, len(index))
    labels = pd.Series(
        latent + rng.normal(0.0, 0.15, len(index)),
        index=index,
        name="label",
    )
    baseline = pd.Series(
        0.65 * latent + rng.normal(0.0, 0.25, len(index)),
        index=index,
        name="score",
    )
    features = pd.DataFrame(
        {
            "quality_a": factor_a,
            "quality_dup": factor_dup,
            "noise": factor_noise,
        },
        index=index,
    )
    return features, labels, baseline


def test_factor_engine_scores_clusters_incremental_value_and_screening() -> None:
    features, labels, baseline = _panel()
    registry = FactorRegistry(
        registry_id="test_registry_v1",
        factors=(
            FactorDefinition("quality_a", "quality", 1),
            FactorDefinition("quality_dup", "quality", 1),
            FactorDefinition("noise", "noise", 1),
        ),
    )
    policy = FactorEvaluationPolicy(
        min_cross_section=30,
        min_coverage=0.95,
        min_oriented_ic=0.20,
        min_oriented_rank_ic=0.20,
        min_oriented_rank_icir=0.50,
        max_rank_turnover=0.50,
        max_pairwise_rank_corr=0.90,
        min_incremental_rank_ic=-0.01,
        require_incremental=True,
    )

    result = evaluate_factors(
        features,
        labels,
        registry,
        policy=policy,
        baseline=baseline,
        decay_labels={5: labels * 0.8},
    )
    summary = result.summary.set_index("factor")

    assert summary.loc["quality_a", "coverage_mean"] == 1.0
    assert summary.loc["quality_a", "oriented_rank_ic_mean"] > 0.8
    assert np.isfinite(summary.loc["quality_a", "incremental_rank_ic"])
    assert summary.loc["noise", "decision"] == "REJECT"
    quality_decisions = summary.loc[["quality_a", "quality_dup"], "decision"].tolist()
    assert quality_decisions.count("ADMIT") == 1
    assert quality_decisions.count("REJECT") == 1
    rejected = next(
        name for name in ("quality_a", "quality_dup") if summary.loc[name, "decision"] == "REJECT"
    )
    assert "correlated_with_admitted_factor" in summary.loc[rejected, "decision_reasons"]
    assert set(result.decay["horizon"]) == {1, 5}
    assert any(len(cluster["members"]) == 2 for cluster in result.clusters["clusters"])


def test_factor_direction_is_predeclared_and_not_inferred_from_validation() -> None:
    features, labels, _ = _panel()
    features = features[["quality_a"]].rename(columns={"quality_a": "contrarian"})
    registry = FactorRegistry(
        registry_id="direction_v1",
        factors=(FactorDefinition("contrarian", "contrarian", -1),),
    )
    result = evaluate_factors(
        -features,
        labels,
        registry,
        policy=FactorEvaluationPolicy(
            min_cross_section=30,
            min_coverage=0.95,
            min_oriented_ic=0.10,
            min_oriented_rank_ic=0.10,
            min_oriented_rank_icir=0.20,
            max_rank_turnover=0.50,
        ),
    )
    row = result.summary.iloc[0]
    assert row["direction"] == -1
    assert row["oriented_rank_ic_mean"] > 0.8
    assert row["decision"] == "ADMIT"


def test_factor_evidence_is_immutable_bound_and_holdout_closed(tmp_path, monkeypatch) -> None:
    features, labels, baseline = _panel()
    registry = FactorRegistry(
        registry_id="evidence_registry_v1",
        factors=(FactorDefinition("quality_a", "quality", 1),),
    )
    exposures = pd.DataFrame(
        {"size": np.tile(np.linspace(-1.0, 1.0, 80), 40)},
        index=features.index,
    )
    result = evaluate_factors(
        features[["quality_a"]],
        labels,
        registry,
        exposures=exposures,
        baseline=baseline,
    )
    monkeypatch.setattr(
        evaluation_module,
        "git_revision",
        lambda root: {"commit": "deadbeef", "dirty": False},
    )
    manifest_path = write_factor_evaluation(
        tmp_path,
        result,
        registry=registry,
        policy=FactorEvaluationPolicy(),
        dataset_version_id="ds_123",
        feature_snapshot_id="fs_456",
        label_spec_id="label_789",
        baseline_prediction_id="pred_101",
        repository_root=tmp_path,
    )
    payload = __import__("json").loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["datasetVersionId"] == "ds_123"
    assert payload["featureSnapshotId"] == "fs_456"
    assert payload["codeCommit"] == "deadbeef"
    assert payload["neutralizationExposureColumns"] == ["size"]
    assert payload["finalHoldoutAccessed"] is False
    assert payload["formalCandidateCreated"] is False
    assert (manifest_path.parent / "factor_neutralized.parquet").is_file()

    with np.testing.assert_raises_regex(ValueError, "final holdout"):
        write_factor_evaluation(
            tmp_path,
            result,
            registry=registry,
            policy=FactorEvaluationPolicy(),
            dataset_version_id="ds_123",
            feature_snapshot_id="fs_456",
            label_spec_id="label_789",
            final_holdout_accessed=True,
            repository_root=tmp_path,
        )
