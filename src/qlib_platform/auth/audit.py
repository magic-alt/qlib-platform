from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SENSITIVE_FRAGMENTS = ("password", "secret", "token", "authorization", "credential")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _safe_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    result = dict(metadata or {})
    for key in result:
        normalized = str(key).lower()
        if any(fragment in normalized for fragment in _SENSITIVE_FRAGMENTS):
            raise ValueError(f"audit metadata must not contain sensitive field {key!r}")
    try:
        _canonical_json(result)
    except (TypeError, ValueError) as exc:
        raise ValueError("audit metadata must be JSON serializable") from exc
    return result


@dataclass(frozen=True)
class AuditEvent:
    sequence: int
    event_id: str
    created_at_utc: str
    actor_subject: str
    action: str
    resource_kind: str
    resource_id: str
    outcome: str
    metadata: dict[str, Any]
    previous_hash: str
    event_hash: str


@dataclass(frozen=True)
class AuditVerification:
    event_count: int
    head_hash: str


class TamperEvidentAuditLog:
    """Append-only API backed by a SHA-256 hash chain.

    The chain detects mutation/reordering. Persisting the returned head hash in external WORM
    storage additionally makes tail truncation detectable by passing it to ``verify``.
    """

    def __init__(self, database: str | Path):
        self.database = Path(database).expanduser().resolve()

    def initialize(self) -> None:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS audit_events (
                    sequence INTEGER PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    created_at_utc TEXT NOT NULL,
                    actor_subject TEXT NOT NULL,
                    action TEXT NOT NULL,
                    resource_kind TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE
                )"""
            )

    def append(
        self,
        *,
        actor_subject: str,
        action: str,
        resource_kind: str,
        resource_id: str,
        outcome: str,
        metadata: Mapping[str, Any] | None = None,
        created_at_utc: str | None = None,
        event_id: str | None = None,
    ) -> AuditEvent:
        self.initialize()
        fields = {
            "actor_subject": actor_subject.strip(),
            "action": action.strip(),
            "resource_kind": resource_kind.strip(),
            "resource_id": resource_id.strip(),
            "outcome": outcome.strip().upper(),
        }
        if any(not value for value in fields.values()):
            raise ValueError("audit actor/action/resource/outcome fields must be non-empty")
        safe_metadata = _safe_metadata(metadata)
        timestamp = created_at_utc or _utc_now()
        identifier = event_id or uuid.uuid4().hex

        connection = sqlite3.connect(self.database)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT sequence,event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence = int(row[0]) + 1 if row else 1
            previous_hash = str(row[1]) if row else "0" * 64
            body = {
                "sequence": sequence,
                "event_id": identifier,
                "created_at_utc": timestamp,
                **fields,
                "metadata": safe_metadata,
                "previous_hash": previous_hash,
            }
            event_hash = hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()
            connection.execute(
                """INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    sequence,
                    identifier,
                    timestamp,
                    fields["actor_subject"],
                    fields["action"],
                    fields["resource_kind"],
                    fields["resource_id"],
                    fields["outcome"],
                    _canonical_json(safe_metadata),
                    previous_hash,
                    event_hash,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        return AuditEvent(
            sequence=sequence,
            event_id=identifier,
            created_at_utc=timestamp,
            actor_subject=fields["actor_subject"],
            action=fields["action"],
            resource_kind=fields["resource_kind"],
            resource_id=fields["resource_id"],
            outcome=fields["outcome"],
            metadata=safe_metadata,
            previous_hash=previous_hash,
            event_hash=event_hash,
        )

    def events(self) -> list[AuditEvent]:
        self.initialize()
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(
                """SELECT sequence,event_id,created_at_utc,actor_subject,action,resource_kind,
                resource_id,outcome,metadata_json,previous_hash,event_hash
                FROM audit_events ORDER BY sequence"""
            ).fetchall()
        return [
            AuditEvent(
                sequence=int(row[0]),
                event_id=str(row[1]),
                created_at_utc=str(row[2]),
                actor_subject=str(row[3]),
                action=str(row[4]),
                resource_kind=str(row[5]),
                resource_id=str(row[6]),
                outcome=str(row[7]),
                metadata=json.loads(str(row[8])),
                previous_hash=str(row[9]),
                event_hash=str(row[10]),
            )
            for row in rows
        ]

    def verify(self, *, expected_head_hash: str | None = None) -> AuditVerification:
        previous_hash = "0" * 64
        events = self.events()
        for expected_sequence, event in enumerate(events, start=1):
            if event.sequence != expected_sequence or event.previous_hash != previous_hash:
                raise ValueError("audit chain sequence or previous hash is invalid")
            body = {
                "sequence": event.sequence,
                "event_id": event.event_id,
                "created_at_utc": event.created_at_utc,
                "actor_subject": event.actor_subject,
                "action": event.action,
                "resource_kind": event.resource_kind,
                "resource_id": event.resource_id,
                "outcome": event.outcome,
                "metadata": event.metadata,
                "previous_hash": event.previous_hash,
            }
            actual = hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()
            if actual != event.event_hash:
                raise ValueError(f"audit chain hash mismatch at sequence {event.sequence}")
            previous_hash = actual
        if expected_head_hash is not None and previous_hash != expected_head_hash:
            raise ValueError("audit chain head does not match external anchor")
        return AuditVerification(event_count=len(events), head_hash=previous_hash)
