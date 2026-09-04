from pathlib import Path
import pickle

import pandas as pd

from qlib_platform.backtest_audit import audit_mlflow_run


def test_audit_rejects_report_before_predictions(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    (artifacts / "portfolio_analysis").mkdir(parents=True)
    index = pd.MultiIndex.from_product(
        [[pd.Timestamp("2026-01-05")], ["SH600000"]], names=["datetime", "instrument"]
    )
    pd.DataFrame({"score": [1.0]}, index=index).to_pickle(artifacts / "pred.pkl")
    pd.DataFrame(
        {"account": [1.0, 1.1], "return": [0.0, 0.1], "bench": [0.0, 0.01]},
        index=pd.to_datetime(["2025-12-31", "2026-01-05"]),
    ).to_pickle(artifacts / "portfolio_analysis" / "report_normal_1day.pkl")
    with (artifacts / "config").open("wb") as fp:
        pickle.dump({"benchmark": "BJ920000"}, fp)

    report = audit_mlflow_run(tmp_path)

    assert report["passed"] is False
    assert "portfolio_starts_before_predictions" in report["errors"]
    assert "uncertified_benchmark:BJ920000" in report["errors"]
