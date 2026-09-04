from __future__ import annotations

import pandas as pd
import pytest

from qlib_platform.backtesting.execution_audit import reconcile_execution, require_reconciliation


def _audit() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": ["2026-01-05", "2026-01-06"],
            "actual_action": ["BUY", "SELL"],
            "filled_quantity": [100.0, 100.0],
            "filled_value": [1_000.0, 1_100.0],
            "trade_cost": [1.0, 2.0],
            "quantity_before": [0.0, 100.0],
            "quantity_after": [100.0, 0.0],
        }
    )


def _report() -> pd.DataFrame:
    return pd.DataFrame(
        {"total_turnover": [1_000.0, 2_100.0], "total_cost": [1.0, 3.0]},
        index=pd.to_datetime(["2026-01-05", "2026-01-06"]),
    )


def test_reconciliation_requires_buy_sell_turnover_cost_and_inventory_to_match() -> None:
    daily, result = reconcile_execution(_audit(), _report())

    assert result.passed
    assert result.filled_turnover == 2_100.0
    assert result.filled_cost == 3.0
    assert daily["passed"].all()


def test_reconciliation_rejects_missing_sell_from_audit() -> None:
    audit = _audit().iloc[[0]].copy()
    _, result = reconcile_execution(audit, _report())

    assert not result.passed
    with pytest.raises(RuntimeError, match="AUDIT_RECONCILIATION_FAILED"):
        require_reconciliation(result)
