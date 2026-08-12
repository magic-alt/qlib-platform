from __future__ import annotations

from pathlib import Path

import pytest

from tushare_qlib.ops_state import DeliveryStatus, OpsState


def _deployment(identifier: str) -> dict[str, object]:
    return {
        "deploymentId": identifier,
        "researchRunId": "research-1",
        "modelFamily": "lightgbm",
        "refitAsOf": "2026-08-10",
        "trainEndDate": "2026-01-01",
        "createdAtUtc": "2026-08-10T10:00:00Z",
    }


def test_registry_activation_and_rollback_are_transactional(tmp_path: Path):
    state = OpsState(tmp_path / "ops.sqlite3")
    state.register_deployment(_deployment("model-a"), "artifact://deployment/model-a/model_manifest.json", "a")
    state.register_deployment(_deployment("model-b"), "artifact://deployment/model-b/model_manifest.json", "b")

    state.deploy("model-a")
    assert state.current_deployment()["deployment_id"] == "model-a"
    state.deploy("model-b")
    assert state.deployment("model-a")["status"] == "RETIRED"
    assert state.current_deployment()["deployment_id"] == "model-b"
    state.deploy("model-a", reason="rollback")
    assert state.current_deployment()["deployment_id"] == "model-a"
    assert state.deployment("model-b")["status"] == "RETIRED"


def test_signal_revision_conflict_is_fail_closed(tmp_path: Path):
    state = OpsState(tmp_path / "ops.sqlite3")
    state.register_deployment(_deployment("model-a"), "artifact://deployment/model-a/model_manifest.json", "a")
    state.deploy("model-a")
    record = {
        "signal_id": "signal-a",
        "signal_date": "2026-08-10",
        "trade_date": "2026-08-11",
        "deployment_id": "model-a",
        "dataset_sha256": "dataset-a",
        "signal_sha256": "score-a",
        "manifest_uri": "artifact://signal/signal-a/manifest.json",
        "status": "PASS",
    }
    assert state.register_signal(record)
    assert not state.register_signal(record)
    changed = {**record, "signal_id": "signal-b", "signal_sha256": "score-b"}
    with pytest.raises(ValueError, match="different PASS signal"):
        state.register_signal(changed)
    assert state.register_signal(changed, supersede=True)
    assert state.signal_for_trade_date("2026-08-11")["signal_id"] == "signal-b"


def test_failed_delivery_can_retry_but_sent_delivery_is_suppressed(tmp_path: Path):
    state = OpsState(tmp_path / "ops.sqlite3")
    record = {
        "idempotency_key": "delivery-1",
        "message_id": "message-1",
        "channel": "feishu",
        "message_kind": "SIGNAL_PREVIEW",
        "business_date": "2026-08-10",
        "payload_sha256": "payload",
    }
    assert state.reserve_delivery(record)
    state.record_delivery_attempt("delivery-1", DeliveryStatus.FAILED, error_code="TIMEOUT")
    assert state.reserve_delivery(record)
    state.record_delivery_attempt("delivery-1", DeliveryStatus.SENT)
    assert not state.reserve_delivery(record)
