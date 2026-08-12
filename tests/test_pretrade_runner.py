from __future__ import annotations

import pandas as pd

from tushare_qlib.pretrade_runner import _action_envelope


def test_successful_empty_pretrade_decision_is_explicit_no_action():
    record = {
        "signal_id": "signal-1",
        "signal_date": "2026-08-10",
        "trade_date": "2026-08-11",
        "deployment_id": "model-1",
        "signal_sha256": "score-1",
    }
    decision = pd.DataFrame(columns=["target_action", "instrument", "action_reason"])
    orders = pd.DataFrame(columns=["side", "instrument", "quantity", "action_reason"])
    blocked = pd.DataFrame(columns=["instrument", "reason"])

    envelope = _action_envelope(record, decision, orders, blocked)

    assert envelope.message_kind == "NO_ACTION"
    assert "运行成功" in envelope.summary
    assert envelope.signal_date == "2026-08-10"
