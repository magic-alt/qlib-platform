from tushare_qlib.research_gate import ResearchThresholds, evaluate_research_metrics


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
