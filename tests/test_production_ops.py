from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from tushare_qlib.ops_state import DeliveryStatus, OpsState


def _deployment(identifier: str) -> dict[str, object]:
    return {
        "deploymentId": identifier,
        "researchRunId": "research-1",
        "modelFamily": "lightgbm",
        "refitAsOf": "2026-08-10",
        "trainEndDate": "2026-01-01",
        "trainStartDate": "2020-01-01",
        "datasetId": "dataset-1",
        "featureSchemaSha256": "feature-sha",
        "modelBinarySha256": "model-sha",
        "lineage": {
            "qlibPlatform": {"commit": "platform-commit"},
            "qlib": {"commit": "qlib-commit"},
        },
        "createdAtUtc": "2026-08-10T10:00:00Z",
    }


def test_registry_activation_and_rollback_are_transactional(tmp_path: Path):
    state = OpsState(tmp_path / "ops.sqlite3")
    state.register_deployment(_deployment("model-a"), "artifact://deployment/model-a/model_manifest.json", "a")
    state.register_deployment(_deployment("model-b"), "artifact://deployment/model-b/model_manifest.json", "b")

    state.deploy("model-a")
    current = state.current_deployment()
    assert current["deployment_id"] == "model-a"
    assert current["dataset_id"] == "dataset-1"
    assert current["platform_commit"] == "platform-commit"
    assert current["qlib_commit"] == "qlib-commit"
    assert current["deployed_at_utc"]
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


def test_pending_delivery_lease_prevents_concurrent_duplicate(tmp_path: Path):
    state = OpsState(tmp_path / "ops.sqlite3")
    record = {
        "idempotency_key": "delivery-lease",
        "message_id": "message-lease",
        "channel": "feishu",
        "message_kind": "SIGNAL_PREVIEW",
        "business_date": "2026-08-10",
        "signal_date": "2026-08-10",
        "trade_date": "2026-08-11",
        "deployment_id": "model-a",
        "signal_sha256": "score-a",
        "payload_sha256": "payload-a",
    }

    assert state.reserve_delivery(record, owner="worker-a", lease_seconds=60)
    assert not state.reserve_delivery(record, owner="worker-b", lease_seconds=60)
    assert not state.reserve_delivery(record, owner="worker-a", lease_seconds=60)

    with state.reading() as connection:
        row = connection.execute(
            "SELECT signal_date, trade_date, deployment_id, signal_sha256, lease_owner FROM deliveries"
        ).fetchone()
    assert tuple(row) == ("2026-08-10", "2026-08-11", "model-a", "score-a", "worker-a")

    with state.transaction() as connection:
        connection.execute(
            "UPDATE deliveries SET lease_expires_at_utc = '2000-01-01T00:00:00Z' WHERE idempotency_key = ?",
            (record["idempotency_key"],),
        )
    assert state.reserve_delivery(record, owner="worker-b", lease_seconds=60)


def test_ops_state_migrates_v1_delivery_and_deployment_tables(tmp_path: Path):
    path = tmp_path / "ops.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta VALUES('schema_version', '1');
            CREATE TABLE deployments (
                deployment_id TEXT PRIMARY KEY, research_run_id TEXT NOT NULL,
                family TEXT NOT NULL, bundle_uri TEXT NOT NULL, bundle_sha256 TEXT NOT NULL,
                refit_as_of TEXT NOT NULL, train_end_date TEXT NOT NULL, created_at_utc TEXT NOT NULL,
                status TEXT NOT NULL, status_at_utc TEXT NOT NULL, metadata_json TEXT NOT NULL
            );
            CREATE TABLE deliveries (
                idempotency_key TEXT PRIMARY KEY, message_id TEXT NOT NULL, channel TEXT NOT NULL,
                message_kind TEXT NOT NULL, business_date TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
                status TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0, created_at_utc TEXT NOT NULL,
                sent_at_utc TEXT, error_code TEXT, error_summary TEXT
            );
            """
        )

    state = OpsState(path)

    with state.reading() as connection:
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        delivery_columns = {row[1] for row in connection.execute("PRAGMA table_info(deliveries)")}
        deployment_columns = {row[1] for row in connection.execute("PRAGMA table_info(deployments)")}
    assert version == "2"
    assert {"signal_date", "trade_date", "lease_owner", "lease_expires_at_utc"} <= delivery_columns
    assert {"dataset_id", "model_binary_sha256", "platform_commit", "deployed_at_utc"} <= deployment_columns
