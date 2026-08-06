from tushare_qlib.research_gate import evaluate_research_metrics


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
