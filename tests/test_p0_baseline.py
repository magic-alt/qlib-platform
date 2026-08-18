from __future__ import annotations

import pandas as pd
import pytest

from tushare_qlib.p0_baseline import cost_stress_test


def test_cost_stress_reports_extra_friction_and_net_excess() -> None:
    report = pd.DataFrame(
        {
            "account": [100_000.0, 100_100.0],
            "return": [0.0, 0.001],
            "bench": [0.0, 0.0],
            "cost": [0.0, 0.0],
        },
        index=pd.to_datetime(["2026-01-05", "2026-01-06"]),
    )
    audit = pd.DataFrame(
        {
            "trade_date": ["2026-01-05", "2026-01-06"],
            "filled_value": [1_000.0, 1_100.0],
        }
    )

    stress = cost_stress_test(report, audit, extra_bps=(0.0, 1.0))

    assert stress["extra_slippage_bps"].tolist() == [0.0, 1.0]
    assert stress.iloc[1]["additional_cost"] == pytest.approx(0.21)
    assert stress.iloc[1]["net_excess_return"] < stress.iloc[0]["net_excess_return"]
