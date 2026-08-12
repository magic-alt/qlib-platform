from __future__ import annotations

import json
from pathlib import Path

import pytest

from tushare_qlib.delivery_ledger import DeliveryService
from tushare_qlib.notifier import FeishuNotifier, NotificationDeliveryError, NotificationEnvelope
from tushare_qlib.ops_state import OpsState


def _envelope() -> NotificationEnvelope:
    return NotificationEnvelope(
        message_id="message-1",
        message_kind="SIGNAL_PREVIEW",
        business_date="2026-08-10",
        trade_date="2026-08-11",
        channel="feishu",
        title="Signal PASS",
        summary="No action",
        deployment_id="model-1",
        signal_sha256="score-1",
    )


def test_feishu_delivery_is_idempotent(tmp_path: Path):
    requests = []

    def transport(request, timeout):
        requests.append((request, timeout))
        return json.dumps({"code": 0}).encode()

    notifier = FeishuNotifier("https://example.invalid/hook", transport=transport)
    service = DeliveryService(OpsState(tmp_path / "ops.sqlite3"), retry_delays=(0,))
    assert service.deliver(_envelope(), notifier)
    assert not service.deliver(_envelope(), notifier)
    assert len(requests) == 1


def test_failed_delivery_is_recorded_and_retriable(tmp_path: Path):
    attempts = 0

    def transport(request, timeout):
        nonlocal attempts
        attempts += 1
        return json.dumps({"code": 1}).encode()

    notifier = FeishuNotifier("https://example.invalid/hook", transport=transport)
    service = DeliveryService(OpsState(tmp_path / "ops.sqlite3"), retry_delays=(0,))
    with pytest.raises(NotificationDeliveryError):
        service.deliver(_envelope(), notifier)
    with pytest.raises(NotificationDeliveryError):
        service.deliver(_envelope(), notifier)
    assert attempts == 2
