from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, Mapping


OPS_SCHEMA_VERSION = 2


class DeploymentStatus(str, Enum):
    STAGED = "STAGED"
    DEPLOYED = "DEPLOYED"
    RETIRED = "RETIRED"
    REJECTED = "REJECTED"


class RunStatus(str, Enum):
    RUNNING = "RUNNING"
    PASS = "PASS"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class SignalStatus(str, Enum):
    GENERATED = "GENERATED"
    PASS = "PASS"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class DeliveryStatus(str, Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class OpsState:
    """Transactional production state shared by deployment, signals and delivery."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def reading(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    def _migrate(self) -> None:
        with self.transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS deployments (
                    deployment_id TEXT PRIMARY KEY,
                    research_run_id TEXT NOT NULL,
                    dataset_id TEXT,
                    family TEXT NOT NULL,
                    bundle_uri TEXT NOT NULL,
                    bundle_sha256 TEXT NOT NULL,
                    refit_as_of TEXT NOT NULL,
                    train_start_date TEXT,
                    train_end_date TEXT NOT NULL,
                    feature_schema_sha256 TEXT,
                    model_binary_sha256 TEXT,
                    platform_commit TEXT,
                    qlib_commit TEXT,
                    created_at_utc TEXT NOT NULL,
                    deployed_at_utc TEXT,
                    status TEXT NOT NULL,
                    status_at_utc TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_deployed_model
                    ON deployments(status) WHERE status = 'DEPLOYED';
                CREATE TABLE IF NOT EXISTS deployment_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    deployment_id TEXT NOT NULL REFERENCES deployments(deployment_id),
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    event_at_utc TEXT NOT NULL,
                    reason TEXT
                );
                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    run_id TEXT PRIMARY KEY,
                    phase TEXT NOT NULL,
                    business_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at_utc TEXT NOT NULL,
                    finished_at_utc TEXT,
                    details_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS signals (
                    signal_id TEXT PRIMARY KEY,
                    signal_date TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    deployment_id TEXT NOT NULL REFERENCES deployments(deployment_id),
                    dataset_sha256 TEXT NOT NULL,
                    signal_sha256 TEXT NOT NULL,
                    manifest_uri TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    superseded_by TEXT,
                    UNIQUE(signal_date, signal_sha256)
                );
                CREATE INDEX IF NOT EXISTS signal_trade_date ON signals(trade_date, status);
                CREATE TABLE IF NOT EXISTS deliveries (
                    idempotency_key TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    message_kind TEXT NOT NULL,
                    business_date TEXT NOT NULL,
                    signal_date TEXT,
                    trade_date TEXT,
                    deployment_id TEXT,
                    signal_sha256 TEXT,
                    payload_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    created_at_utc TEXT NOT NULL,
                    sent_at_utc TEXT,
                    last_attempt_at_utc TEXT,
                    error_code TEXT,
                    error_summary TEXT,
                    lease_owner TEXT,
                    lease_expires_at_utc TEXT
                );
                """
            )
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(deliveries)").fetchall()
            }
            for name, declaration in {
                "signal_date": "TEXT",
                "trade_date": "TEXT",
                "deployment_id": "TEXT",
                "signal_sha256": "TEXT",
                "last_attempt_at_utc": "TEXT",
                "lease_owner": "TEXT",
                "lease_expires_at_utc": "TEXT",
            }.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE deliveries ADD COLUMN {name} {declaration}")
            deployment_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(deployments)").fetchall()
            }
            for name, declaration in {
                "dataset_id": "TEXT",
                "train_start_date": "TEXT",
                "feature_schema_sha256": "TEXT",
                "model_binary_sha256": "TEXT",
                "platform_commit": "TEXT",
                "qlib_commit": "TEXT",
                "deployed_at_utc": "TEXT",
            }.items():
                if name not in deployment_columns:
                    connection.execute(f"ALTER TABLE deployments ADD COLUMN {name} {declaration}")
            version = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            if version is not None and int(version["value"]) > OPS_SCHEMA_VERSION:
                raise RuntimeError(f"ops database schema {version['value']} is newer than supported")
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
                (str(OPS_SCHEMA_VERSION),),
            )

    def register_deployment(self, manifest: Mapping[str, Any], bundle_uri: str, bundle_sha256: str) -> None:
        deployment_id = str(manifest["deploymentId"])
        created = str(manifest.get("createdAtUtc") or _utc_now())
        lineage = manifest.get("lineage", {})
        lineage = lineage if isinstance(lineage, Mapping) else {}
        platform = lineage.get("qlibPlatform", {})
        qlib = lineage.get("qlib", {})
        platform_commit = platform.get("commit") if isinstance(platform, Mapping) else None
        qlib_commit = qlib.get("commit") if isinstance(qlib, Mapping) else None
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT bundle_sha256 FROM deployments WHERE deployment_id = ?", (deployment_id,)
            ).fetchone()
            if existing is not None:
                if existing["bundle_sha256"] != bundle_sha256:
                    raise ValueError("deployment id collision with different bundle checksum")
                return
            connection.execute(
                """
                INSERT INTO deployments(
                    deployment_id, research_run_id, dataset_id, family, bundle_uri, bundle_sha256,
                    refit_as_of, train_start_date, train_end_date, feature_schema_sha256,
                    model_binary_sha256, platform_commit, qlib_commit, created_at_utc,
                    status, status_at_utc, metadata_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'STAGED', ?, ?)
                """,
                (
                    deployment_id,
                    str(manifest["researchRunId"]),
                    str(manifest.get("datasetId", "")),
                    str(manifest["modelFamily"]),
                    bundle_uri,
                    bundle_sha256,
                    str(manifest["refitAsOf"]),
                    str(manifest.get("trainStartDate", "")),
                    str(manifest["trainEndDate"]),
                    str(manifest.get("featureSchemaSha256", "")),
                    str(manifest.get("modelBinarySha256", "")),
                    platform_commit,
                    qlib_commit,
                    created,
                    created,
                    json.dumps(dict(manifest), sort_keys=True, ensure_ascii=False, default=str),
                ),
            )
            connection.execute(
                "INSERT INTO deployment_events(deployment_id, from_status, to_status, event_at_utc, reason) VALUES(?, NULL, 'STAGED', ?, ?)",
                (deployment_id, created, "production refit completed"),
            )

    def deployment(self, deployment_id: str) -> dict[str, Any]:
        with self.reading() as connection:
            row = connection.execute(
                "SELECT * FROM deployments WHERE deployment_id = ?", (deployment_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown deployment: {deployment_id}")
        return dict(row)

    def current_deployment(self) -> dict[str, Any]:
        with self.reading() as connection:
            row = connection.execute("SELECT * FROM deployments WHERE status = 'DEPLOYED'").fetchone()
        if row is None:
            raise RuntimeError("no DEPLOYED model is registered")
        return dict(row)

    def deploy(self, deployment_id: str, *, reason: str = "manual activation") -> None:
        now = _utc_now()
        with self.transaction() as connection:
            target = connection.execute(
                "SELECT status FROM deployments WHERE deployment_id = ?", (deployment_id,)
            ).fetchone()
            if target is None:
                raise KeyError(f"unknown deployment: {deployment_id}")
            if target["status"] not in {DeploymentStatus.STAGED.value, DeploymentStatus.RETIRED.value}:
                raise ValueError(f"deployment {deployment_id} cannot be activated from {target['status']}")
            current = connection.execute(
                "SELECT deployment_id FROM deployments WHERE status = 'DEPLOYED'"
            ).fetchone()
            if current is not None:
                current_id = str(current["deployment_id"])
                connection.execute(
                    "UPDATE deployments SET status = 'RETIRED', status_at_utc = ? WHERE deployment_id = ?",
                    (now, current_id),
                )
                connection.execute(
                    "INSERT INTO deployment_events(deployment_id, from_status, to_status, event_at_utc, reason) VALUES(?, 'DEPLOYED', 'RETIRED', ?, ?)",
                    (current_id, now, f"superseded by {deployment_id}"),
                )
            connection.execute(
                "UPDATE deployments SET status = 'DEPLOYED', status_at_utc = ?, deployed_at_utc = ? WHERE deployment_id = ?",
                (now, now, deployment_id),
            )
            connection.execute(
                "INSERT INTO deployment_events(deployment_id, from_status, to_status, event_at_utc, reason) VALUES(?, ?, 'DEPLOYED', ?, ?)",
                (deployment_id, str(target["status"]), now, reason),
            )

    def start_run(self, run_id: str, phase: str, business_date: str, details: Mapping[str, Any] | None = None) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO pipeline_runs(run_id, phase, business_date, status, started_at_utc, details_json) VALUES(?, ?, ?, 'RUNNING', ?, ?)",
                (run_id, phase, business_date, _utc_now(), json.dumps(details or {}, sort_keys=True)),
            )

    def finish_run(self, run_id: str, status: RunStatus, details: Mapping[str, Any]) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE pipeline_runs SET status = ?, finished_at_utc = ?, details_json = ? WHERE run_id = ?",
                (status.value, _utc_now(), json.dumps(dict(details), sort_keys=True, default=str), run_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown pipeline run: {run_id}")

    def register_signal(self, record: Mapping[str, str], *, supersede: bool = False) -> bool:
        """Register a signal; return False for an exact idempotent replay."""

        with self.transaction() as connection:
            same = connection.execute(
                "SELECT signal_id FROM signals WHERE signal_date = ? AND signal_sha256 = ?",
                (record["signal_date"], record["signal_sha256"]),
            ).fetchone()
            if same is not None:
                if str(same["signal_id"]) != record["signal_id"]:
                    raise ValueError("signal checksum collision")
                return False
            active = connection.execute(
                "SELECT signal_id FROM signals WHERE signal_date = ? AND status = 'PASS'",
                (record["signal_date"],),
            ).fetchone()
            if active is not None and not supersede:
                raise ValueError("a different PASS signal already exists for this signal date")
            if active is not None:
                connection.execute(
                    "UPDATE signals SET status = 'SUPERSEDED', superseded_by = ? WHERE signal_id = ?",
                    (record["signal_id"], active["signal_id"]),
                )
            connection.execute(
                """
                INSERT INTO signals(
                    signal_id, signal_date, trade_date, deployment_id, dataset_sha256,
                    signal_sha256, manifest_uri, status, created_at_utc
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["signal_id"], record["signal_date"], record["trade_date"],
                    record["deployment_id"], record["dataset_sha256"], record["signal_sha256"],
                    record["manifest_uri"], record["status"], _utc_now(),
                ),
            )
        return True

    def signal_for_trade_date(self, trade_date: str) -> dict[str, Any]:
        with self.reading() as connection:
            rows = connection.execute(
                "SELECT * FROM signals WHERE trade_date = ? AND status = 'PASS' ORDER BY created_at_utc DESC",
                (trade_date,),
            ).fetchall()
        if len(rows) != 1:
            raise RuntimeError(f"expected exactly one PASS signal for {trade_date}, found {len(rows)}")
        return dict(rows[0])

    def reserve_delivery(
        self,
        record: Mapping[str, str],
        *,
        owner: str = "legacy",
        lease_seconds: float = 300.0,
    ) -> bool:
        if lease_seconds <= 0:
            raise ValueError("delivery lease_seconds must be positive")
        now = datetime.now(timezone.utc)
        now_text = now.isoformat().replace("+00:00", "Z")
        lease_expires = (now + timedelta(seconds=lease_seconds)).isoformat().replace("+00:00", "Z")
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT status, lease_owner, lease_expires_at_utc FROM deliveries WHERE idempotency_key = ?",
                (record["idempotency_key"],),
            ).fetchone()
            if existing is not None:
                if existing["status"] == DeliveryStatus.SENT.value:
                    return False
                lease_until = existing["lease_expires_at_utc"]
                if (
                    existing["status"] == DeliveryStatus.PENDING.value
                    and lease_until
                    and str(lease_until) > now_text
                ):
                    return False
                connection.execute(
                    """
                    UPDATE deliveries
                    SET status = 'PENDING', lease_owner = ?, lease_expires_at_utc = ?
                    WHERE idempotency_key = ?
                    """,
                    (owner, lease_expires, record["idempotency_key"]),
                )
                return True
            connection.execute(
                """
                INSERT INTO deliveries(
                    idempotency_key, message_id, channel, message_kind, business_date,
                    signal_date, trade_date, deployment_id, signal_sha256, payload_sha256,
                    status, created_at_utc, lease_owner, lease_expires_at_utc
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?)
                """,
                (
                    record["idempotency_key"], record["message_id"], record["channel"],
                    record["message_kind"], record["business_date"], record.get("signal_date"),
                    record.get("trade_date"), record.get("deployment_id"), record.get("signal_sha256"),
                    record["payload_sha256"], now_text, owner, lease_expires,
                ),
            )
        return True

    def record_delivery_attempt(
        self,
        idempotency_key: str,
        status: DeliveryStatus,
        *,
        error_code: str | None = None,
        error_summary: str | None = None,
        release_lease: bool = True,
    ) -> None:
        sent_at = _utc_now() if status is DeliveryStatus.SENT else None
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE deliveries
                SET status = ?, attempt_count = attempt_count + 1, sent_at_utc = ?,
                    last_attempt_at_utc = ?, error_code = ?, error_summary = ?,
                    lease_owner = CASE WHEN ? THEN NULL ELSE lease_owner END,
                    lease_expires_at_utc = CASE WHEN ? THEN NULL ELSE lease_expires_at_utc END
                WHERE idempotency_key = ?
                """,
                (
                    status.value,
                    sent_at,
                    _utc_now(),
                    error_code,
                    error_summary,
                    release_lease,
                    release_lease,
                    idempotency_key,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown delivery: {idempotency_key}")
