from __future__ import annotations

import pandas as pd
import pytest

from qlib_platform.backtesting.execution_attribution import (
    attribute_strategy_execution,
    summarize_strategy_execution_attribution,
)
from qlib_platform.backtesting.strategy_sdk import ResearchCostModel


def _audit() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "instrument": ["A", "B", "C", "D", "E"],
            "target_action": ["BUY", "BUY", "SELL", "HOLD", "BUY"],
            "actual_action": ["BUY", "HOLD", "SELL", "HOLD", "SELL"],
            "order_requested": [True, True, True, False, True],
            "requested_quantity": [100.0, 100.0, 100.0, 0.0, 100.0],
            "filled_quantity": [100.0, 0.0, 40.0, 0.0, 100.0],
            "filled_value": [1_000.0, 0.0, 400.0, 0.0, 500.0],
            "trade_cost": [1.0, 0.0, 0.6, 0.0, 0.7],
            "execution_status": ["FILLED", "UNFILLED", "PARTIAL", "NOT_REQUESTED", "FILLED"],
        }
    )


def test_attribution_classifies_blocked_partial_mismatch_and_turnover_drift() -> None:
    result = attribute_strategy_execution(_audit())

    assert result.set_index("instrument")["execution_gap"].to_dict() == {
        "A": "MATCHED",
        "B": "BLOCKED",
        "C": "PARTIAL",
        "D": "NO_ACTION",
        "E": "ACTION_MISMATCH",
    }
    assert result.set_index("instrument").at["B", "turnover_drift_ratio"] == -1.0
    assert result.set_index("instrument").at["C", "turnover_drift_ratio"] == pytest.approx(-0.6)
    assert result["cost_drift"].isna().all()
    assert result["cost_drift_status"].eq("UNAVAILABLE_UNCALIBRATED").all()

    summary = summarize_strategy_execution_attribution(result)
    assert summary.matched == 1
    assert summary.blocked == 1
    assert summary.partial == 1
    assert summary.action_mismatch == 1
    assert summary.requested_quantity == 400.0
    assert summary.filled_quantity == 240.0
    assert summary.turnover_drift_ratio == pytest.approx(-0.4)
    assert summary.expected_cost is None
    assert summary.cost_drift is None
    assert summary.cost_model_status == "UNAVAILABLE_UNCALIBRATED"
    assert summary.to_manifest()["rows"] == 5


def test_attribution_calculates_cost_drift_only_with_explicit_model() -> None:
    result = attribute_strategy_execution(
        _audit(),
        cost_model=ResearchCostModel(
            model_id="ten-bps-test",
            expected_cost_bps=10.0,
            source="unit-test",
        ),
    )

    expected = result.set_index("instrument")["expected_trade_cost"]
    assert expected.at["A"] == pytest.approx(1.0)
    assert expected.at["C"] == pytest.approx(0.4)
    assert result["cost_drift_status"].eq("CALCULATED").all()

    summary = summarize_strategy_execution_attribution(result)
    assert summary.actual_cost == pytest.approx(2.3)
    assert summary.expected_cost == pytest.approx(1.9)
    assert summary.cost_drift == pytest.approx(0.4)
    assert summary.cost_model_status == "CALIBRATED"


def test_attribution_handles_zero_sized_strategy_trade_as_full_turnover_loss() -> None:
    audit = _audit().iloc[[0]].copy()
    audit["order_requested"] = False
    audit["requested_quantity"] = 0.0
    audit["filled_quantity"] = 0.0
    audit["filled_value"] = 0.0
    audit["trade_cost"] = 0.0
    audit["actual_action"] = "HOLD"
    audit["execution_status"] = "NOT_REQUESTED"

    result = attribute_strategy_execution(audit)
    summary = summarize_strategy_execution_attribution(result)

    assert result.iloc[0]["execution_gap"] == "BLOCKED"
    assert result.iloc[0]["fill_ratio"] == 0.0
    assert result.iloc[0]["turnover_drift_ratio"] == -1.0
    assert summary.turnover_drift_ratio is None


def test_attribution_contracts_reject_incomplete_frames() -> None:
    with pytest.raises(ValueError, match="strategy audit missing columns"):
        attribute_strategy_execution(pd.DataFrame({"target_action": ["BUY"]}))

    with pytest.raises(ValueError, match="attribution missing columns"):
        summarize_strategy_execution_attribution(pd.DataFrame({"execution_gap": ["MATCHED"]}))
