from __future__ import annotations

import time
import uuid
from typing import Protocol, Sequence

from qlib_platform.notifier import NotificationDeliveryError, NotificationEnvelope
from qlib_platform.ops.ops_state import DeliveryStatus, OpsState


class Notifier(Protocol):
    channel: str

    def send(self, envelope: NotificationEnvelope) -> None: ...


class DeliveryService:
    def __init__(
        self,
        state: OpsState,
        *,
        retry_delays: Sequence[float] = (2.0, 10.0, 30.0),
        lease_seconds: float = 300.0,
    ) -> None:
        self.state = state
        self.retry_delays = tuple(float(value) for value in retry_delays)
        self.lease_seconds = float(lease_seconds)

    def deliver(self, envelope: NotificationEnvelope, notifier: Notifier) -> bool:
        if envelope.channel != notifier.channel:
            raise ValueError("notification envelope channel does not match notifier")
        reserved = self.state.reserve_delivery(
            {
                "idempotency_key": envelope.idempotency_key,
                "message_id": envelope.message_id,
                "channel": envelope.channel,
                "message_kind": envelope.message_kind,
                "business_date": envelope.business_date,
                "signal_date": envelope.signal_date or envelope.business_date,
                "trade_date": envelope.trade_date,
                "deployment_id": envelope.deployment_id or "",
                "signal_sha256": envelope.signal_sha256 or "",
                "payload_sha256": envelope.payload_sha256,
            },
            owner=f"delivery-{uuid.uuid4().hex}",
            lease_seconds=self.lease_seconds,
        )
        if not reserved:
            return False
        attempts = max(1, len(self.retry_delays))
        last_error: NotificationDeliveryError | None = None
        for attempt in range(attempts):
            try:
                notifier.send(envelope)
            except NotificationDeliveryError as exc:
                last_error = exc
                final_attempt = attempt + 1 >= attempts
                self.state.record_delivery_attempt(
                    envelope.idempotency_key,
                    DeliveryStatus.FAILED if final_attempt else DeliveryStatus.PENDING,
                    error_code=type(exc).__name__,
                    error_summary=str(exc)[:200],
                    release_lease=final_attempt,
                )
                if not final_attempt:
                    time.sleep(self.retry_delays[attempt])
            else:
                self.state.record_delivery_attempt(envelope.idempotency_key, DeliveryStatus.SENT)
                return True
        raise NotificationDeliveryError(str(last_error or "notification delivery failed"))
