import numpy as np
import pandas as pd

from qlib_platform.backtesting.strategies import blend_model_scores, momentum_quality_lowvol_signals


def test_rank_ensemble_and_rule_strategy():
    ensemble = blend_model_scores(
        pd.DataFrame({"instrument": ["A", "B"], "model_a": [1, 2], "model_b": [3, 1]}),
        {"model_a": 0.7, "model_b": 0.3},
    )
    assert list(ensemble.columns) == ["instrument", "score", "strategy"]

    dates = pd.bdate_range("2026-01-01", periods=90)
    rows = []
    for instrument, drift in [("SH600000", 0.001), ("SZ000001", 0.0005)]:
        for i, date in enumerate(dates):
            rows.append(
                {"date": date, "instrument": instrument, "close": 10 * np.exp(drift * i), "money": 1e8}
            )
    signals = momentum_quality_lowvol_signals(pd.DataFrame(rows))
    assert len(signals) == 2
    assert signals["score"].notna().all()
