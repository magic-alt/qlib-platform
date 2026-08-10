from __future__ import annotations

import pandas as pd
import pytest

from tushare_qlib.broker_state import record_broker_event
from tushare_qlib.freshness import SnapshotFreshnessError, validate_execution_snapshot
from tushare_qlib.risk_engine import HardRiskPolicy, RiskLimitError, pretrade_risk_check


def test_stale_snapshot_fails_closed():
    frame = pd.DataFrame({"as_of_trade_date": ["2026-08-10"], "snapshot_at_utc": ["2026-08-10T00:00:00Z"]})
    with pytest.raises(SnapshotFreshnessError, match="stale"):
        validate_execution_snapshot(frame, name="quotes", trade_date="2026-08-10", max_age_seconds=60,
                                    now_utc=pd.Timestamp("2026-08-10T00:02:00Z").to_pydatetime())


def test_hard_risk_rejects_concentrated_target():
    targets = pd.DataFrame({"target_weight": [0.5, 0.4], "sector": ["bank", "bank"]})
    with pytest.raises(RiskLimitError, match="hard risk rejected"):
        pretrade_risk_check(targets, HardRiskPolicy(), daily_pnl_pct=0.0)


def test_broker_ledger_rejects_terminal_reopen(tmp_path):
    ledger = tmp_path / "broker_events.parquet"
    record_broker_event(ledger, "id-1", "INTENT", event_at_utc="2026-08-10T01:00:00Z")
    record_broker_event(ledger, "id-1", "SUBMITTED", event_at_utc="2026-08-10T01:01:00Z")
    record_broker_event(
        ledger,
        "id-1",
        "FILLED",
        event_at_utc="2026-08-10T01:02:00Z",
        fill_qty=100,
        fill_price=10,
    )
    with pytest.raises(ValueError, match="illegal"):
        record_broker_event(ledger, "id-1", "SUBMITTED", event_at_utc="2026-08-10T01:03:00Z")
