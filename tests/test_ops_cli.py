from __future__ import annotations

import json
from pathlib import Path

import pytest

from qlib_platform.ops.ops_cli import export_daily_ops
from qlib_platform.ops.ops_state import DeliveryStatus, OpsState, RunStatus
from qlib_platform.settings import Paths, Settings


def _settings(tmp_path: Path) -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    return Settings(tmp_path / "pipeline.yaml", {}, paths, None, None, tmp_path / "qlib")


def _delivery() -> dict[str, str]:
    return {
        "idempotency_key": "delivery-1",
        "message_id": "message-1",
        "channel": "feishu",
        "message_kind": "PIPELINE_FAILED",
        "business_date": "2026-08-11",
        "payload_sha256": "payload-1",
    }


def test_ops_query_recovery_ack_and_daily_export(tmp_path: Path):
    settings = _settings(tmp_path)
    state = OpsState(settings.paths.state / "ops.sqlite3")
    state.start_run("run-1", "PRETRADE", "2026-08-11")
    state.finish_run("run-1", RunStatus.FAILED, {"errorCode": "QUOTE_STALE"})
    assert state.reserve_delivery(_delivery())
    state.record_delivery_attempt("delivery-1", DeliveryStatus.FAILED, error_code="TIMEOUT")

    assert state.list_runs(business_date="2026-08-11", status="FAILED")[0]["run_id"] == "run-1"
    state.recover_delivery("delivery-1")
    state.acknowledge("run", "run-1", operator="operator-a", reason="incident reviewed")
    output = export_daily_ops(settings, "2026-08-11", tmp_path / "daily.json")
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["runs"]["counts"] == {"FAILED": 1}
    assert payload["deliveries"]["counts"] == {"FAILED": 1}
    assert payload["acknowledgements"][0]["operator"] == "operator-a"


def test_ops_recovery_refuses_sent_and_active_pending(tmp_path: Path):
    state = OpsState(tmp_path / "ops.sqlite3")
    assert state.reserve_delivery(_delivery(), owner="worker", lease_seconds=60)
    with pytest.raises(ValueError, match="active PENDING"):
        state.recover_delivery("delivery-1")
    state.record_delivery_attempt("delivery-1", DeliveryStatus.SENT)
    with pytest.raises(ValueError, match="SENT"):
        state.recover_delivery("delivery-1")
