from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..store import sha256_file


@dataclass(frozen=True)
class OutboxItem:
    item_id: str
    artifact_path: Path
    artifact_sha256: str
    data_release_id: str
    status: str
    attempts: int


class ArtifactOutbox:
    """Durable optional handoff queue; research never waits for platform availability."""

    def __init__(self, database: str | Path):
        self.database = Path(database).expanduser().resolve()

    def initialize(self) -> None:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS artifact_outbox (
                    item_id TEXT PRIMARY KEY,
                    artifact_path TEXT NOT NULL,
                    artifact_sha256 TEXT NOT NULL,
                    data_release_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at_utc TEXT NOT NULL,
                    acknowledged_at_utc TEXT,
                    UNIQUE(artifact_sha256, data_release_id)
                )"""
            )

    def enqueue(self, artifact: str | Path, data_release_id: str) -> OutboxItem:
        path = Path(artifact).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        if not data_release_id.startswith("ds_") or len(data_release_id) != 67:
            raise ValueError("outbox artifact must be bound to a valid DataRelease id")
        self.initialize()
        digest = sha256_file(path)
        item_id = uuid.uuid4().hex
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """INSERT OR IGNORE INTO artifact_outbox(
                    item_id,artifact_path,artifact_sha256,data_release_id,status,created_at_utc
                ) VALUES(?,?,?,?,?,?)""",
                (
                    item_id,
                    str(path),
                    digest,
                    data_release_id,
                    "PENDING",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM artifact_outbox WHERE artifact_sha256=? AND data_release_id=?",
                (digest, data_release_id),
            ).fetchone()
        assert row is not None
        return self._item(row)

    def pending(self) -> list[OutboxItem]:
        self.initialize()
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(
                "SELECT * FROM artifact_outbox WHERE status!='ACK' ORDER BY created_at_utc"
            ).fetchall()
        return [self._item(row) for row in rows]

    def drain(self, sender: Callable[[OutboxItem], None]) -> int:
        acknowledged = 0
        for item in self.pending():
            path = item.artifact_path
            if not path.is_file() or sha256_file(path) != item.artifact_sha256:
                self._failed(item.item_id, "artifact checksum mismatch")
                continue
            try:
                sender(item)
            except Exception as exc:
                self._failed(item.item_id, type(exc).__name__)
                continue
            with sqlite3.connect(self.database) as connection:
                connection.execute(
                    """UPDATE artifact_outbox SET status='ACK',attempts=attempts+1,
                       last_error=NULL,acknowledged_at_utc=? WHERE item_id=?""",
                    (datetime.now(timezone.utc).isoformat(), item.item_id),
                )
            acknowledged += 1
        return acknowledged

    def _failed(self, item_id: str, error: str) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE artifact_outbox SET status='PENDING',attempts=attempts+1,last_error=? WHERE item_id=?",
                (error[:200], item_id),
            )

    @staticmethod
    def _item(row: tuple[object, ...]) -> OutboxItem:
        return OutboxItem(
            str(row[0]), Path(str(row[1])), str(row[2]), str(row[3]), str(row[4]), int(str(row[5]))
        )
