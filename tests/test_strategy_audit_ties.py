from __future__ import annotations

import pandas as pd

from tushare_qlib.strategy_audit import _orders_match_or_tie_equivalent


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
