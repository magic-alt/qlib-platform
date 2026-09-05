from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from qlib_platform.research.evidence.experiment_db import Connection, json_payload, utc_now


class ExperimentArtifactWriteMixin:
    _db: Connection

    def register_portfolio(
        self,
        portfolio_id: str,
        *,
        experiment_id: str,
        asof_date: str,
        policy: str,
        metrics: Mapping[str, Any] | None = None,
        artifact_uri: str | None = None,
        artifact_sha256: str | None = None,
    ) -> None:
        self._db.execute(
            """INSERT INTO portfolios (
            portfolio_id, experiment_id, asof_date, policy, metrics_json,
            artifact_uri, artifact_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (portfolio_id) DO UPDATE SET experiment_id = EXCLUDED.experiment_id,
            asof_date = EXCLUDED.asof_date, policy = EXCLUDED.policy,
            metrics_json = EXCLUDED.metrics_json, artifact_uri = EXCLUDED.artifact_uri,
            artifact_sha256 = EXCLUDED.artifact_sha256""",
            (
                portfolio_id,
                experiment_id,
                asof_date,
                policy,
                json_payload(metrics),
                artifact_uri,
                artifact_sha256,
                utc_now(),
            ),
        )

    def register_artifact(
        self,
        experiment_id: str,
        *,
        kind: str,
        uri: str,
        sha256: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self._db.execute(
            """INSERT INTO artifacts (experiment_id, kind, uri, sha256, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (experiment_id, kind, sha256) DO UPDATE SET
            uri = EXCLUDED.uri, metadata_json = EXCLUDED.metadata_json""",
            (experiment_id, kind, uri, sha256, json_payload(metadata), utc_now()),
        )
