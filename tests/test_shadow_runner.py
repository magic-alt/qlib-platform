from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from tushare_qlib.pretrade_runner import PretradeResult
from tushare_qlib.settings import Paths, Settings
from tushare_qlib.shadow_runner import run_shadow, simulate_order_lifecycle


def _orders() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "client_order_id": "order-1",
                "instrument": "SH600000",
                "side": "BUY",
                "quantity": 100,
                "limit_price": 10.0,
                "estimated_notional": 1000.0,
                "estimated_fees": 5.0,
            }
        ]
    )


def test_simulated_order_lifecycle_is_deterministic_and_has_no_submit_state():
    first = simulate_order_lifecycle(_orders(), trade_date="2026-08-11")
    second = simulate_order_lifecycle(_orders(), trade_date="2026-08-11")

    pd.testing.assert_frame_equal(first, second)
    assert first["event_type"].tolist() == ["INTENT_CREATED", "SIM_ACCEPTED", "SIM_FILLED"]
    assert first.iloc[-1]["fill_quantity"] == 100
    assert not first["event_type"].str.contains("SUBMIT", regex=False).any()


def test_shadow_runner_persists_lifecycle_and_cumulative_metrics(tmp_path: Path, monkeypatch):
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    settings = Settings(tmp_path / "pipeline.yaml", {}, paths, None, None, tmp_path / "qlib")
    pretrade_root = tmp_path / "pretrade"
    pretrade_root.mkdir()
    orders_path = pretrade_root / "orders.parquet"
    blocked_path = pretrade_root / "blocked.csv"
    decision_path = pretrade_root / "decision.parquet"
    _orders().to_parquet(orders_path, index=False)
    pd.DataFrame(columns=["instrument", "reason"]).to_csv(blocked_path, index=False)
    pd.DataFrame().to_parquet(decision_path, index=False)
    monkeypatch.setattr(
        "tushare_qlib.shadow_runner.run_pretrade_actions",
        lambda *args, **kwargs: PretradeResult(
            "signal-1", "2026-08-11", decision_path, orders_path, blocked_path
        ),
    )

    result = run_shadow(settings, trade_date="2026-08-11")
    metrics = json.loads(result.metrics_path.read_text(encoding="utf-8"))
    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))

    assert metrics["mode"] == "SHADOW"
    assert metrics["brokerSubmitEnabled"] is False
    assert metrics["filledCount"] == 1
    assert summary == {"days": 1, "orders": 1, "fills": 1, "blocked": 0, "grossNotional": 1000.0}
