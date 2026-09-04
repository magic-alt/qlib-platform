from __future__ import annotations

import pandas as pd
import pytest

from qlib_platform.signal_diagnostics import build_signal_diagnostics


def test_signal_diagnostics_emit_ic_rank_ic_rolls_months_and_prediction_autocorrelation() -> None:
    index = pd.MultiIndex.from_product(
        [pd.date_range("2026-01-02", periods=3, freq="B"), ["SH600000", "SZ000001", "SZ000002"]],
        names=["datetime", "instrument"],
    )
    predictions = pd.Series([3.0, 2.0, 1.0] * 3, index=index)
    labels = pd.Series([0.03, 0.02, 0.01] * 3, index=index)

    daily, summary = build_signal_diagnostics(predictions, labels, rolling_window=2)

    assert {"ic", "rank_ic", "rolling_ic_63d", "rolling_rank_ic_63d", "top_bottom_spread"}.issubset(
        daily.columns
    )
    assert summary["ic"] == pytest.approx(1.0)
    assert summary["rankIC"] == pytest.approx(1.0)
    assert len(summary["monthly"]) == 1
