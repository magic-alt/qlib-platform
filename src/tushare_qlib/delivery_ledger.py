from __future__ import annotations

import time
from typing import Protocol, Sequence

from .notifier import NotificationDeliveryError, NotificationEnvelope
from .ops_state import DeliveryStatus, OpsState


class Notifier(Protocol):
    channel: str

    def send(self, envelope: NotificationEnvelope) -> None: ...


class DeliveryService:
    def __init__(self, state: OpsState, *, retry_delays: Sequence[float] = (2.0, 10.0, 30.0)) -> None:
        self.state = state
        self.retry_delays = tuple(float(value) for value in retry_delays)

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
                "payload_sha256": envelope.payload_sha256,
            }
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
                self.state.record_delivery_attempt(
                    envelope.idempotency_key,
                    DeliveryStatus.FAILED,
                    error_code=type(exc).__name__,
                    error_summary=str(exc)[:200],
                )
                if attempt + 1 < attempts:
                    time.sleep(self.retry_delays[attempt])
            else:
                self.state.record_delivery_attempt(envelope.idempotency_key, DeliveryStatus.SENT)
                return True
        raise NotificationDeliveryError(str(last_error or "notification delivery failed"))
