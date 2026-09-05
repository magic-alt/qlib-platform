from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

from qlib_platform.research.evidence.experiment_db import Connection, json_payload, utc_now


class ExperimentQlibFederationMixin:
    """Persistence surface for references to upstream Qlib Recorder runs."""

    _db: Connection

    def register_qlib_recorder(
        self,
        experiment_id: str,
        *,
        recorder_id: str,
        qlib_experiment_id: str,
        recorder_name: str | None,
        status: str,
        tracking_uri: str | None,
        artifact_uri: str | None,
        start_time: str | None,
        end_time: str | None,
        params: Mapping[str, Any] | None = None,
        tags: Mapping[str, Any] | None = None,
        metrics: Mapping[str, Any] | None = None,
    ) -> None:
        if not experiment_id.strip() or not recorder_id.strip() or not qlib_experiment_id.strip():
            raise ValueError("experiment_id, qlib_experiment_id, and recorder_id are required")
        self._db.execute(
            """INSERT INTO qlib_recorders (
            experiment_id, recorder_id, qlib_experiment_id, recorder_name, status,
            tracking_uri, artifact_uri, start_time, end_time, params_json, tags_json,
            metrics_json, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (experiment_id) DO UPDATE SET
            recorder_id = EXCLUDED.recorder_id,
            qlib_experiment_id = EXCLUDED.qlib_experiment_id,
            recorder_name = EXCLUDED.recorder_name,
            status = EXCLUDED.status,
            tracking_uri = EXCLUDED.tracking_uri,
            artifact_uri = EXCLUDED.artifact_uri,
            start_time = EXCLUDED.start_time,
            end_time = EXCLUDED.end_time,
            params_json = EXCLUDED.params_json,
            tags_json = EXCLUDED.tags_json,
            metrics_json = EXCLUDED.metrics_json,
            synced_at = EXCLUDED.synced_at""",
            (
                experiment_id,
                recorder_id,
                qlib_experiment_id,
                recorder_name,
                status,
                tracking_uri,
                artifact_uri,
                start_time,
                end_time,
                json_payload(params),
                json_payload(tags),
                json_payload(metrics),
                utc_now(),
            ),
        )

    def list_qlib_recorders(self) -> pd.DataFrame:
        return self._db.fetch_frame(
            """SELECT experiment_id, recorder_id, qlib_experiment_id, recorder_name,
            status, tracking_uri, artifact_uri, start_time, end_time, synced_at
            FROM qlib_recorders ORDER BY synced_at DESC"""
        )
