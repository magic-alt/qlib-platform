from __future__ import annotations

import pandas as pd

from tushare_qlib.strategy_audit import _orders_match_or_tie_equivalent
from tushare_qlib.topk_dropout import TopkDropoutPolicy


def test_order_match_accepts_exact_tie_substitution() -> None:
    scores = pd.Series({"SH600000": 0.4, "SZ000001": 0.4, "SZ000002": 0.3})
    assert _orders_match_or_tie_equivalent({"SH600000": "BUY"}, {"SZ000001": "BUY"}, scores)


def test_order_match_rejects_non_equivalent_substitution_or_direction() -> None:
    scores = pd.Series({"SH600000": 0.4, "SZ000001": 0.3})
    assert not _orders_match_or_tie_equivalent({"SH600000": "BUY"}, {"SZ000001": "BUY"}, scores)
    assert not _orders_match_or_tie_equivalent({"SH600000": "BUY"}, {"SH600000": "SELL"}, scores)


def test_order_match_rejects_missing_scores_and_unknown_actions() -> None:
    scores = pd.Series({"SH600000": 0.4})
    assert not _orders_match_or_tie_equivalent({"SH600000": "BUY"}, {"SZ000001": "BUY"}, scores)
    assert not _orders_match_or_tie_equivalent({"SH600000": "HOLD"}, {"SH600000": "HOLD"}, scores)


def test_order_match_accepts_tied_sell_selected_on_ineligible_holding() -> None:
    scores = pd.Series(
        {
            "SZ000001": 0.0032,
            "SZ000999": 0.0056,
            "SH600018": 0.0056,
            "SH601229": 0.0185,
            "SH601766": 0.0185,
        }
    )
    positions = pd.DataFrame(
        {
            "instrument": ["SZ000001", "SZ000999", "SH600018"],
            "quantity": [100.0, 100.0, 100.0],
            "holding_days": [7, 8, 4],
        }
    )

    assert _orders_match_or_tie_equivalent(
        {
            "SZ000001": "SELL",
            "SZ000999": "SELL",
            "SH601229": "BUY",
        },
        {"SZ000001": "SELL", "SH601766": "BUY"},
        scores,
        positions=positions,
        policy=TopkDropoutPolicy(hold_thresh=5),
    )


def test_order_match_rejects_missing_sell_without_blocked_tie() -> None:
    scores = pd.Series({"SZ000001": 0.0032, "SZ000999": 0.0056, "SH600018": 0.0056})
    positions = pd.DataFrame(
        {
            "instrument": ["SZ000001", "SZ000999", "SH600018"],
            "quantity": [100.0, 100.0, 100.0],
            "holding_days": [7, 8, 5],
        }
    )

    assert not _orders_match_or_tie_equivalent(
        {"SZ000001": "SELL", "SZ000999": "SELL"},
        {"SZ000001": "SELL"},
        scores,
        positions=positions,
        policy=TopkDropoutPolicy(hold_thresh=5),
    )
