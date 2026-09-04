import math
from pathlib import Path

import pandas as pd
import pytest

from qlib_platform.research.reporting.summary import render_markdown, summarize_matrix


def test_summary_combines_signal_and_prediction_portfolio_metrics(tmp_path: Path) -> None:
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    research_manifest = research_dir / "manifest.json"
    research_manifest.write_text(
        '{"metrics":{"ic_mean":0.02,"rank_ic_mean":0.03,"icir":0.4,"rank_icir":0.5}}',
        encoding="utf-8",
    )

    portfolio_dir = tmp_path / "portfolio"
    portfolio_dir.mkdir()
    report_path = portfolio_dir / "portfolio_report.parquet"
    pd.DataFrame(
        {
            "return": [0.01, -0.02, 0.015],
            "bench": [0.005, -0.01, 0.004],
            "cost": [0.001, 0.001, 0.001],
            "turnover": [0.10, 0.20, 0.15],
        }
    ).to_parquet(report_path)
    portfolio_manifest = portfolio_dir / "manifest.json"
    portfolio_manifest.write_text(
        '{"metrics":{"returnTotal":0.01,"benchTotal":0.0,"costTotal":0.003},'
        '"artifacts":[{"name":"portfolio_report.parquet","localPath":"'
        + str(report_path).replace("\\", "\\\\")
        + '"}]}',
        encoding="utf-8",
    )

    matrix = tmp_path / "research_matrix.json"
    matrix.write_text(
        '{"datasetRef":"standalone-current","mode":"fixed","stage":"signal","jobs":[{'
        '"alphaPack":"alpha158_market_v1","model":"lightgbm","status":"SUCCEEDED",'
        '"result":{"manifest":"'
        + str(research_manifest).replace("\\", "\\\\")
        + '"},"predictionBacktest":{"result":{"manifest":"'
        + str(portfolio_manifest).replace("\\", "\\\\")
        + '"}}}]}',
        encoding="utf-8",
    )

    summary = summarize_matrix(matrix)
    job = summary["jobs"][0]
    assert job["icMean"] == pytest.approx(0.02)
    assert job["rankIcir"] == pytest.approx(0.5)
    assert math.isfinite(float(job["excessIr"]))
    assert job["maxDrawdown"] < 0
    assert job["turnoverMean"] == pytest.approx(0.15)
    assert "Alpha158" not in render_markdown(summary)
    assert "alpha158_market_v1" in render_markdown(summary)
