from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from qlib_platform.ops.delivery_ledger import DeliveryService
from qlib_platform.notifier import FeishuNotifier, NotificationDeliveryError, NotificationEnvelope
from qlib_platform.ops.ops_state import OpsState


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


def test_concurrent_delivery_sends_exactly_once(tmp_path: Path):
    sent = 0
    sent_lock = threading.Lock()
    barrier = threading.Barrier(2)

    def transport(request, timeout):
        nonlocal sent
        with sent_lock:
            sent += 1
        time.sleep(0.05)
        return json.dumps({"code": 0}).encode()

    state = OpsState(tmp_path / "ops.sqlite3")

    def deliver():
        notifier = FeishuNotifier("https://example.invalid/hook", transport=transport)
        service = DeliveryService(state, retry_delays=(0,), lease_seconds=60)
        barrier.wait()
        return service.deliver(_envelope(), notifier)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: deliver(), range(2)))

    assert sorted(results) == [False, True]
    assert sent == 1


def test_retry_keeps_lease_until_delivery_finishes(tmp_path: Path):
    first_failed = threading.Event()
    attempts = 0

    def transport(request, timeout):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            first_failed.set()
            return json.dumps({"code": 1}).encode()
        return json.dumps({"code": 0}).encode()

    state = OpsState(tmp_path / "ops.sqlite3")
    notifier = FeishuNotifier("https://example.invalid/hook", transport=transport)
    service = DeliveryService(state, retry_delays=(0.1, 0.1), lease_seconds=60)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(service.deliver, _envelope(), notifier)
        assert first_failed.wait(timeout=2)
        competing = DeliveryService(state, retry_delays=(0,), lease_seconds=60)
        assert not competing.deliver(_envelope(), notifier)
        assert future.result(timeout=2)

    assert attempts == 2
