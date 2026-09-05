from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from qlib_platform.research.evidence.experiment_db import Connection, json_payload, utc_now


class ExperimentWriteMixin:
    _db: Connection

    def register_experiment(
        self,
        experiment_id: str,
        *,
        status: str = "CREATED",
        dataset_id: str | None = None,
        feature_set_id: str | None = None,
        model_id: str | None = None,
        portfolio_id: str | None = None,
        git_sha: str | None = None,
        params: Mapping[str, Any] | None = None,
        lineage: Mapping[str, Any] | None = None,
        created_at: str | None = None,
    ) -> None:
        if not experiment_id.strip():
            raise ValueError("experiment_id must not be empty")
        now = created_at or utc_now()
        self._db.execute(
            """INSERT INTO experiments (
            experiment_id, created_at, updated_at, status, dataset_id, feature_set_id,
            model_id, portfolio_id, git_sha, params_json, lineage_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (experiment_id) DO UPDATE SET
            updated_at = EXCLUDED.updated_at, status = EXCLUDED.status,
            dataset_id = EXCLUDED.dataset_id, feature_set_id = EXCLUDED.feature_set_id,
            model_id = EXCLUDED.model_id, portfolio_id = EXCLUDED.portfolio_id,
            git_sha = EXCLUDED.git_sha, params_json = EXCLUDED.params_json,
            lineage_json = EXCLUDED.lineage_json""",
            (
                experiment_id,
                now,
                now,
                status,
                dataset_id,
                feature_set_id,
                model_id,
                portfolio_id,
                git_sha,
                json_payload(params),
                json_payload(lineage),
            ),
        )

    def log_metrics(
        self,
        experiment_id: str,
        metrics: Mapping[str, float],
        *,
        split: str = "oos",
        step: int = 0,
    ) -> None:
        now = utc_now()
        for name, value in metrics.items():
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ValueError(f"metric {name!r} is not finite")
            self._db.execute(
                """INSERT INTO metrics (experiment_id, metric_name, split, step, value, created_at)
                VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (experiment_id, metric_name, split, step)
                DO UPDATE SET value = EXCLUDED.value, created_at = EXCLUDED.created_at""",
                (experiment_id, str(name), split, int(step), numeric, now),
            )

    def register_model(
        self,
        model_id: str,
        *,
        family: str,
        config: Mapping[str, Any],
        artifact_uri: str | None = None,
        artifact_sha256: str | None = None,
    ) -> None:
        self._db.execute(
            """INSERT INTO models
            (model_id, family, config_json, artifact_uri, artifact_sha256, created_at)
            VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (model_id) DO UPDATE SET
            family = EXCLUDED.family, config_json = EXCLUDED.config_json,
            artifact_uri = EXCLUDED.artifact_uri, artifact_sha256 = EXCLUDED.artifact_sha256""",
            (model_id, family, json_payload(config), artifact_uri, artifact_sha256, utc_now()),
        )

    def register_factor(
        self,
        factor_id: str,
        *,
        name: str,
        definition_sha256: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self._db.execute(
            """INSERT INTO factors (factor_id, name, definition_sha256, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?) ON CONFLICT (factor_id) DO UPDATE SET
            name = EXCLUDED.name, definition_sha256 = EXCLUDED.definition_sha256,
            metadata_json = EXCLUDED.metadata_json""",
            (factor_id, name, definition_sha256, json_payload(metadata), utc_now()),
        )
