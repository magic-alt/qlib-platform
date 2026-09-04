from __future__ import annotations

from pathlib import Path

import pandas as pd

from qlib_platform.live_parity import compare_research_live_scores


def test_research_live_parity_compares_score_rank_and_topk(tmp_path: Path):
    index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2026-08-10"), "A"),
            (pd.Timestamp("2026-08-10"), "B"),
            (pd.Timestamp("2026-08-10"), "C"),
        ],
        names=["datetime", "instrument"],
    )
    research = tmp_path / "research.parquet"
    pd.DataFrame({"score": [0.3, 0.2, 0.1]}, index=index).to_parquet(research)
    live = tmp_path / "live.parquet"
    pd.DataFrame(
        {
            "signal_date": ["2026-08-10"] * 3,
            "instrument": ["C", "A", "B"],
            "score": [0.1, 0.3, 0.2],
        }
    ).to_parquet(live, index=False)

    report = compare_research_live_scores(
        research, live, signal_date="2026-08-10", topk=2, output_path=tmp_path / "parity.json"
    )

    assert report["passed"]
    assert report["researchScoreSha256"] == report["liveScoreSha256"]
    assert report["researchTopkSha256"] == report["liveTopkSha256"]


def test_research_live_parity_rejects_rank_change(tmp_path: Path):
    research = tmp_path / "research.csv"
    live = tmp_path / "live.csv"
    pd.DataFrame({"signal_date": ["2026-08-10"] * 2, "instrument": ["A", "B"], "score": [0.2, 0.1]}).to_csv(
        research, index=False
    )
    pd.DataFrame({"signal_date": ["2026-08-10"] * 2, "instrument": ["A", "B"], "score": [0.1, 0.2]}).to_csv(
        live, index=False
    )

    report = compare_research_live_scores(research, live, signal_date="2026-08-10", topk=1)

    assert not report["passed"]
    assert report["researchTopkSha256"] != report["liveTopkSha256"]
