from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


class _Indicator:
    def __init__(self) -> None:
        self.order_indicator_his = {
            pd.Timestamp("2026-01-05"): {
                "amount": pd.Series({"SH600000": 100.0, "SZ000001": -200.0}),
                "deal_amount": pd.Series({"SH600000": 100.0, "SZ000001": -200.0}),
                "trade_dir": pd.Series({"SH600000": 1.0, "SZ000001": -1.0}),
                "trade_price": pd.Series({"SH600000": 10.0, "SZ000001": 8.0}),
                "trade_value": pd.Series({"SH600000": 1_000.0, "SZ000001": -1_600.0}),
                "trade_cost": pd.Series({"SH600000": 1.0, "SZ000001": -2.0}),
                "ffr": pd.Series({"SH600000": 1.0, "SZ000001": 1.0}),
            }
        }


def test_qrun_export_retains_negative_direction_sell_orders() -> None:
    path = Path(__file__).parents[1] / "scripts" / "export_qrun_backtest_report.py"
    spec = importlib.util.spec_from_file_location("qrun_report_export", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    audit = module.extract_trade_audit(_Indicator())

    assert audit["actual_action"].tolist() == ["BUY", "SELL"]
    assert audit["filled_value"].sum() == 2_600.0
    assert audit["trade_cost"].sum() == 3.0
