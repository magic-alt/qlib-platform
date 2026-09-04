from __future__ import annotations

import pandas as pd
import pytest

from qlib_platform.strategy_audit import build_strategy_audit, build_strategy_decision
from qlib_platform.topk_dropout import RankBufferPolicy, TopkDropoutPolicy


class _MetricSeries:
    def __init__(self, values: dict[str, float]) -> None:
        self.index = pd.Index(sorted(values))
        self.data = pd.Series([values[str(code)] for code in self.index], index=self.index)


class _OrderIndicator:
    def __init__(self, metrics: dict[str, dict[str, float]]) -> None:
        self.data = {name: _MetricSeries(values) for name, values in metrics.items()}


class _Indicator:
    def __init__(self, trade_date: pd.Timestamp, *, lowercase: bool = False) -> None:
        def codes(values: dict[str, float]) -> dict[str, float]:
            return {key.lower() if lowercase else key: value for key, value in values.items()}

        self.order_indicator_his = {
            trade_date: _OrderIndicator(
                {
                    "amount": codes({"S22": -100.0, "S01": 100.0, "S02": 100.0, "S04": 100.0}),
                    "deal_amount": codes({"S22": -100.0, "S01": 100.0, "S02": 100.0, "S04": 100.0}),
                    "trade_dir": codes({"S22": -1.0, "S01": 1.0, "S02": 1.0, "S04": 1.0}),
                    "trade_price": codes({"S22": 8.0, "S01": 10.0, "S02": 9.0, "S04": 11.0}),
                    "trade_value": codes({"S22": -800.0, "S01": 1_000.0, "S02": 900.0, "S04": 1_100.0}),
                    "trade_cost": codes({"S22": -1.0, "S01": 1.0, "S02": 1.0, "S04": 1.0}),
                }
            )
        }


class _Position:
    def __init__(self, instruments: list[str]) -> None:
        self._instruments = instruments

    def get_stock_list(self) -> list[str]:
        return self._instruments

    def get_stock_amount(self, instrument: str) -> float:
        return 100.0

    def get_stock_count(self, instrument: str, bar: str) -> int:
        return 3


def test_build_strategy_decision_dispatches_on_policy_type() -> None:
    scores = pd.Series({f"S{rank:02d}": 100 - rank for rank in range(1, 26)})
    positions = pd.DataFrame({"instrument": ["S03", "S22"], "quantity": [100, 100], "holding_days": [3, 3]})
    quotes = pd.DataFrame(
        {
            "instrument": list(scores.index),
            "paused": [0] * len(scores),
            "is_limit_up": [0] * len(scores),
            "is_limit_down": [0] * len(scores),
        }
    )

    topk_decision = build_strategy_decision(
        scores,
        positions,
        quotes,
        policy=TopkDropoutPolicy(topk=10, n_drop=3, hold_thresh=1),
        signal_date=pd.Timestamp("2026-01-05"),
        trade_date=pd.Timestamp("2026-01-06"),
    )
    rank_decision = build_strategy_decision(
        scores,
        positions,
        quotes,
        policy=RankBufferPolicy(target_size=10, entry_rank=10, exit_rank=20, max_replacements=3),
        signal_date=pd.Timestamp("2026-01-05"),
        trade_date=pd.Timestamp("2026-01-06"),
    )

    assert "is_model_topk" in topk_decision.columns
    assert "is_model_topk" in rank_decision.columns
    assert set(rank_decision.loc[rank_decision["target_action"] == "SELL", "instrument"]) == {"S22"}


def test_build_strategy_audit_reconciles_rank_buffer_fills() -> None:
    score_index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-01-05"), f"S{rank:02d}") for rank in range(1, 26)],
        names=["datetime", "instrument"],
    )
    scores = pd.Series([100 - rank for rank in range(1, 26)], index=score_index)
    policy = RankBufferPolicy(target_size=10, entry_rank=10, exit_rank=20, max_replacements=3)
    trade_date = pd.Timestamp("2026-01-06")
    positions = {
        pd.Timestamp("2026-01-05"): _Position(["S03", "S22"]),
        trade_date: _Position(["S03", "S01", "S02", "S04"]),
    }
    quotes = pd.DataFrame(
        {
            "trade_date": [trade_date] * 25,
            "instrument": [f"S{rank:02d}" for rank in range(1, 26)],
            "paused": [0] * 25,
            "is_limit_up": [0] * 25,
            "is_limit_down": [0] * 25,
        }
    )

    audit = build_strategy_audit(
        scores.rename(index=lambda value: str(value).lower(), level="instrument"),
        {
            date: _Position([instrument.lower() for instrument in position.get_stock_list()])
            for date, position in positions.items()
        },
        _Indicator(trade_date, lowercase=True),
        quotes.assign(instrument=quotes["instrument"].str.lower()),
        policy=policy,
    )

    assert not audit.empty
    filled = audit.loc[audit["execution_status"].eq("FILLED")]
    assert set(filled["instrument"]) == {"S22", "S01", "S02", "S04"}
    assert set(filled.loc[filled["actual_action"] == "SELL", "instrument"]) == {"S22"}
    assert set(filled.loc[filled["actual_action"] == "BUY", "instrument"]) == {"S01", "S02", "S04"}
    # The audit surfaces the rank-buffer action reasons.
    assert set(audit.loc[audit["target_action"] == "SELL", "action_reason"]) == {"EXIT_RANK_BREACH"}


def test_build_strategy_audit_rejects_rank_buffer_divergence() -> None:
    score_index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-01-05"), f"S{rank:02d}") for rank in range(1, 26)],
        names=["datetime", "instrument"],
    )
    scores = pd.Series([100 - rank for rank in range(1, 26)], index=score_index)
    policy = RankBufferPolicy(target_size=10, entry_rank=10, exit_rank=20, max_replacements=3)
    trade_date = pd.Timestamp("2026-01-06")

    class DivergentIndicator(_Indicator):
        def __init__(self) -> None:
            self.order_indicator_his = {
                trade_date: _OrderIndicator(
                    {
                        "amount": {"S22": -100.0, "S99": 100.0},
                        "deal_amount": {"S22": -100.0, "S99": 100.0},
                        "trade_dir": {"S22": -1.0, "S99": 1.0},
                        "trade_price": {"S22": 8.0, "S99": 10.0},
                        "trade_value": {"S22": -800.0, "S99": 1_000.0},
                        "trade_cost": {"S22": -1.0, "S99": 1.0},
                    }
                )
            }

    positions = {pd.Timestamp("2026-01-05"): _Position(["S03", "S22"]), trade_date: _Position(["S03", "S99"])}
    quotes = pd.DataFrame(
        {
            "trade_date": [trade_date] * 25,
            "instrument": [f"S{rank:02d}" for rank in range(1, 26)],
            "paused": [0] * 25,
            "is_limit_up": [0] * 25,
            "is_limit_down": [0] * 25,
        }
    )

    with pytest.raises(RuntimeError, match="strategy audit diverged"):
        build_strategy_audit(
            scores,
            positions,
            DivergentIndicator(),
            quotes,
            policy=policy,
        )
