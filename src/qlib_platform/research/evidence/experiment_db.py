from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import pandas as pd


SCHEMA_VERSION = 2


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_payload(value: Mapping[str, Any] | Sequence[Any] | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


class Connection(Protocol):
    def execute(self, sql: str, parameters: Sequence[Any] = ()) -> None: ...
    def fetch_frame(self, sql: str, parameters: Sequence[Any] = ()) -> pd.DataFrame: ...
    def close(self) -> None: ...


class DuckDBConnection:
    def __init__(self, path: str | Path) -> None:
        try:
            import duckdb
        except ImportError as exc:  # pragma: no cover - packaged core dependency
            raise RuntimeError("DuckDB experiment storage requires the duckdb package") from exc
        resolved = Path(path).expanduser()
        if resolved != Path(":memory:"):
            resolved.parent.mkdir(parents=True, exist_ok=True)
        self._connection = duckdb.connect(str(resolved))

    def execute(self, sql: str, parameters: Sequence[Any] = ()) -> None:
        self._connection.execute(sql, list(parameters))

    def fetch_frame(self, sql: str, parameters: Sequence[Any] = ()) -> pd.DataFrame:
        return self._connection.execute(sql, list(parameters)).fetchdf()

    def close(self) -> None:
        self._connection.close()


class PostgresConnection:
    def __init__(self, dsn: str) -> None:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("PostgreSQL experiment storage requires qlib-platform[postgres]") from exc
        self._connection = psycopg.connect(dsn, autocommit=True)

    @staticmethod
    def _translate(sql: str) -> str:
        return sql.replace("?", "%s")

    def execute(self, sql: str, parameters: Sequence[Any] = ()) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(self._translate(sql), tuple(parameters))

    def fetch_frame(self, sql: str, parameters: Sequence[Any] = ()) -> pd.DataFrame:
        with self._connection.cursor() as cursor:
            cursor.execute(self._translate(sql), tuple(parameters))
            rows = cursor.fetchall()
            columns = [item.name for item in cursor.description or ()]
        return pd.DataFrame(rows, columns=columns)

    def close(self) -> None:
        self._connection.close()


def open_connection(uri: str | Path) -> tuple[str, Connection]:
    raw = str(uri)
    if raw.startswith("postgresql://") or raw.startswith("postgres://"):
        return "postgresql", PostgresConnection(raw)
    path = raw[len("duckdb:///") :] if raw.startswith("duckdb:///") else raw
    return "duckdb", DuckDBConnection(path)


_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS research_schema (
        singleton_id INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS experiments (
        experiment_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        status TEXT NOT NULL, dataset_id TEXT, feature_set_id TEXT, model_id TEXT,
        portfolio_id TEXT, git_sha TEXT, params_json TEXT NOT NULL, lineage_json TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS metrics (
        experiment_id TEXT NOT NULL, metric_name TEXT NOT NULL, split TEXT NOT NULL,
        step INTEGER NOT NULL, value DOUBLE PRECISION NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY (experiment_id, metric_name, split, step))""",
    """CREATE TABLE IF NOT EXISTS models (
        model_id TEXT PRIMARY KEY, family TEXT NOT NULL, config_json TEXT NOT NULL,
        artifact_uri TEXT, artifact_sha256 TEXT, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS factors (
        factor_id TEXT PRIMARY KEY, name TEXT NOT NULL, definition_sha256 TEXT NOT NULL,
        metadata_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS portfolios (
        portfolio_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, asof_date TEXT NOT NULL,
        policy TEXT NOT NULL, metrics_json TEXT NOT NULL, artifact_uri TEXT,
        artifact_sha256 TEXT, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS artifacts (
        experiment_id TEXT NOT NULL, kind TEXT NOT NULL, uri TEXT NOT NULL, sha256 TEXT NOT NULL,
        metadata_json TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY (experiment_id, kind, sha256))""",
    """CREATE TABLE IF NOT EXISTS qlib_recorders (
        experiment_id TEXT PRIMARY KEY, recorder_id TEXT NOT NULL,
        qlib_experiment_id TEXT NOT NULL, recorder_name TEXT, status TEXT NOT NULL,
        tracking_uri TEXT, artifact_uri TEXT, start_time TEXT, end_time TEXT,
        params_json TEXT NOT NULL, tags_json TEXT NOT NULL, metrics_json TEXT NOT NULL,
        synced_at TEXT NOT NULL)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS qlib_recorders_identity
        ON qlib_recorders (qlib_experiment_id, recorder_id)""",
)


def initialize_schema(connection: Connection) -> None:
    for statement in _SCHEMA_STATEMENTS:
        connection.execute(statement)
    connection.execute(
        """INSERT INTO research_schema (singleton_id, schema_version, updated_at)
        VALUES (1, ?, ?) ON CONFLICT (singleton_id) DO UPDATE SET
        schema_version = EXCLUDED.schema_version, updated_at = EXCLUDED.updated_at""",
        (SCHEMA_VERSION, utc_now()),
    )
