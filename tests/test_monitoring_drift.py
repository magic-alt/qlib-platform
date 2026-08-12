from __future__ import annotations

import pandas as pd

from tushare_qlib.monitoring import evaluate_signal_drift, signal_drift_snapshot


def test_signal_drift_tracks_psi_topk_overlap_and_rank_turnover():
    reference = pd.Series([4.0, 3.0, 2.0, 1.0], index=["A", "B", "C", "D"])
    current = pd.Series([4.1, 2.9, 2.1, 1.1], index=["A", "B", "C", "D"])

    metrics = signal_drift_snapshot(reference, current, topk=2)

    assert metrics["topkOverlap"] == 1.0
    assert metrics["rankTurnover"] == 0.0
    assert metrics["sharedInstrumentCount"] == 4


def test_signal_drift_thresholds_fail_closed():
    reference = pd.Series([4.0, 3.0, 2.0, 1.0], index=["A", "B", "C", "D"])
    current = pd.Series([100.0, 90.0, -90.0, -100.0], index=["D", "C", "B", "A"])

    metrics, reasons = evaluate_signal_drift(
        reference,
        current,
        topk=2,
        max_score_psi=0.01,
        min_topk_overlap=0.5,
        max_rank_turnover=0.2,
    )

    assert metrics["topkOverlap"] == 0.0
    assert {"SCORE_PSI_HIGH", "TOPK_OVERLAP_LOW", "RANK_TURNOVER_HIGH"} <= set(reasons)
