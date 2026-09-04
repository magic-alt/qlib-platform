from __future__ import annotations

import numpy as np
import pandas as pd

from qlib_platform.backtesting.topk_dropout import (
    TopkDropoutPolicy,
    _normalise_quotes,
    topk_dropout_decision,
)


def test_quote_normalisation_matches_qlib_limit_threshold_expressions():
    quotes = pd.DataFrame(
        {
            "instrument": ["A", "B", "C", "D"],
            "paused": [0.0, np.nan, 0.0, 0.0],
            "is_limit_up": [np.nan, 0.0, 2.0, -1.0],
            "is_limit_down": [np.nan, 3.0, 0.0, -1.0],
        }
    )

    normalized = _normalise_quotes(quotes, required=True)

    # Qlib Exchange marks missing close/suspension evidence as non-tradable.
    assert normalized.at["B", "paused"] == 1.0
    # Qlib's configured thresholds are `$is_limit_* > 0`: missing values compare
    # false, every positive value is limited, and zero/negative values are not.
    assert normalized.at["A", "is_limit_up"] == 0.0
    assert normalized.at["A", "is_limit_down"] == 0.0
    assert normalized.at["C", "is_limit_up"] == 1.0
    assert normalized.at["B", "is_limit_down"] == 1.0
    assert normalized.at["D", "is_limit_up"] == 0.0
    assert normalized.at["D", "is_limit_down"] == 0.0


def test_missing_limit_flags_do_not_suppress_qlib_compatible_orders():
    scores = pd.Series({"A": 0.9, "B": 0.8, "C": 0.7, "D": 0.6})
    positions = pd.DataFrame(
        {
            "instrument": ["B", "D"],
            "quantity": [100, 100],
            "holding_days": [5, 5],
        }
    )
    quotes = pd.DataFrame(
        {
            "instrument": list(scores.index),
            "paused": [0.0, 0.0, 0.0, 0.0],
            "is_limit_up": [np.nan, np.nan, np.nan, np.nan],
            "is_limit_down": [np.nan, np.nan, np.nan, np.nan],
        }
    )

    decision = topk_dropout_decision(
        scores,
        positions,
        quotes,
        policy=TopkDropoutPolicy(topk=2, n_drop=1, hold_thresh=5),
    ).set_index("instrument")

    assert decision.at["A", "target_action"] == "BUY"
    assert decision.at["D", "target_action"] == "SELL"
    assert bool(decision.at["A", "candidate_tradable"])
    assert bool(decision.at["D", "candidate_tradable"])
