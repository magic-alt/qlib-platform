import pandas as pd
import pytest

from qlib_platform.research.evaluation.gates import (
    ResearchThresholds,
    derive_research_metrics,
    derive_signal_metrics,
    evaluate_component_metrics,
    evaluate_research_metrics,
    evaluate_signal_metrics,
)


def test_long_short_annualization_respects_label_horizon():
    dates = pd.date_range("2026-01-01", periods=2)
    index = pd.MultiIndex.from_product(
        [dates, ["SH600000", "SH600001", "SH600002", "SH600003", "SH600004"]],
        names=["datetime", "instrument"],
    )
    predictions = pd.Series([5, 4, 3, 2, 1] * 2, index=index)
    labels = pd.Series([0.05, 0.03, 0.0, -0.01, -0.05] * 2, index=index)
    report = pd.DataFrame({"return": [0.0, 0.0], "bench": [0.0, 0.0], "cost": [0.0, 0.0]})

    metrics = derive_research_metrics(
        predictions,
        labels,
        report,
        unique_artifact=True,
        lineage_complete=True,
        label_horizon_days=5,
    )

    assert metrics["long_short_annualized"] == pytest.approx(0.10 * 252 / 5)


def test_signal_screen_uses_signal_metrics_and_never_authorizes_promotion():
    dates = pd.date_range("2026-01-01", periods=3)
    index = pd.MultiIndex.from_product([dates, ["A", "B", "C", "D", "E"]], names=["datetime", "instrument"])
    predictions = pd.Series([5, 4, 3, 2, 1] * 3, index=index)
    labels = pd.Series(
        [
            0.05,
            0.03,
            0.0,
            -0.01,
            -0.05,
            0.05,
            0.02,
            0.01,
            -0.02,
            -0.04,
            0.04,
            0.03,
            -0.01,
            0.0,
            -0.05,
        ],
        index=index,
    )
    metrics = derive_signal_metrics(
        predictions,
        labels,
        unique_artifact=True,
        lineage_complete=True,
        label_horizon_days=5,
    )

    report = evaluate_signal_metrics(
        metrics,
        ResearchThresholds(min_observations=3, min_icir=0.0),
    )

    assert report["passed"] is True
    assert report["decision"] == "SIGNAL_SCREEN_PASS"
    assert report["promotionAuthorized"] is False


def test_short_fold_is_component_validated_without_weakening_release_thresholds():
    metrics = {"observations": 60, "unique_artifact": True, "lineage_complete": True}

    component = evaluate_component_metrics(metrics)
    release = evaluate_research_metrics(metrics, ResearchThresholds(min_observations=252))

    assert component["decision"] == "COMPONENT_VALIDATED"
    assert component["passed"] is True
    assert release["decision"] == "REJECT"


def test_uploaded_sample_metrics_are_rejected():
    report = evaluate_research_metrics(
        {
            "observations": 77,
            "ic_mean": -0.005143,
            "rank_ic_mean": 0.031161,
            "icir": -0.4268,
            "long_short_annualized": -0.01259,
            "excess_ir": -2.948202,
            "max_drawdown": -0.386248,
            "unique_artifact": False,
        }
    )
    assert report["decision"] == "REJECT"
    assert not report["passed"]


def test_complete_positive_metrics_are_promoted():
    report = evaluate_research_metrics(
        {
            "observations": 300,
            "ic_mean": 0.03,
            "rank_ic_mean": 0.04,
            "icir": 0.8,
            "long_short_annualized": 0.10,
            "excess_ir": 0.9,
            "max_drawdown": -0.10,
            "unique_artifact": True,
            "lineage_complete": True,
        },
        ResearchThresholds(),
    )

    assert report["decision"] == "PROMOTE"
    assert report["passed"]


def _healthy_metrics(**overrides: object) -> dict[str, object]:
    return {
        "observations": 300,
        "ic_mean": 0.03,
        "rank_ic_mean": 0.04,
        "icir": 0.8,
        "rank_icir": 0.8,
        "long_short_annualized": 0.10,
        "excess_ir": 0.9,
        "max_drawdown": -0.10,
        "unique_artifact": True,
        "lineage_complete": True,
        **overrides,
    }


def test_rank_icir_can_satisfy_the_production_stability_gate():
    report = evaluate_research_metrics(
        _healthy_metrics(icir=0.283, rank_icir=0.51),
        ResearchThresholds(),
    )

    assert report["decision"] == "PROMOTE"
    assert report["passed"] is True


def test_borderline_stability_is_routed_to_research_review_not_rejection():
    report = evaluate_research_metrics(
        _healthy_metrics(icir=0.283, rank_icir=0.42),
        ResearchThresholds(),
    )

    assert report["decision"] == "RESEARCH_REVIEW"
    assert report["passed"] is False
    assert report["reviewRequired"] is True


def test_missing_lineage_is_always_rejected():
    report = evaluate_research_metrics(
        {
            "observations": 300,
            "ic_mean": 0.03,
            "rank_ic_mean": 0.04,
            "icir": 0.8,
            "long_short_annualized": 0.10,
            "excess_ir": 0.9,
            "max_drawdown": -0.10,
            "unique_artifact": True,
        }
    )

    assert report["decision"] == "REJECT"
    assert next(item for item in report["checks"] if item["name"] == "lineage_complete")["passed"] is False
