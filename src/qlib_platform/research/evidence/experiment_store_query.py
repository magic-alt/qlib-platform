from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pandas as pd

from qlib_platform.research.evidence.experiment_db import Connection


class ExperimentQueryMixin:
    _db: Connection

    def list_experiments(
        self,
        *,
        status: str | None = None,
        dataset_id: str | None = None,
        model_id: str | None = None,
        limit: int = 200,
    ) -> pd.DataFrame:
        if limit <= 0:
            raise ValueError("limit must be positive")
        where: list[str] = []
        params: list[Any] = []
        for column, value in (("status", status), ("dataset_id", dataset_id), ("model_id", model_id)):
            if value is not None:
                where.append(f"{column} = ?")
                params.append(value)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        params.append(int(limit))
        return self._db.fetch_frame(
            f"""SELECT experiment_id, created_at, updated_at, status, dataset_id,
            feature_set_id, model_id, portfolio_id, git_sha FROM experiments {clause}
            ORDER BY created_at DESC, experiment_id LIMIT ?""",
            params,
        )

    def get_experiment(self, experiment_id: str) -> dict[str, Any] | None:
        experiment = self._db.fetch_frame(
            "SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)
        )
        if experiment.empty:
            return None
        metrics = self._db.fetch_frame(
            """SELECT metric_name, split, step, value, created_at FROM metrics
            WHERE experiment_id = ? ORDER BY split, step, metric_name""",
            (experiment_id,),
        )
        artifacts = self._db.fetch_frame(
            """SELECT kind, uri, sha256, metadata_json, created_at FROM artifacts
            WHERE experiment_id = ? ORDER BY kind, created_at""",
            (experiment_id,),
        )
        row = experiment.iloc[0].to_dict()
        for column in ("params_json", "lineage_json"):
            row[column.removesuffix("_json")] = json.loads(str(row.pop(column)))
        row["metrics"] = metrics.to_dict(orient="records")
        row["artifacts"] = artifacts.to_dict(orient="records")
        return row

    def compare_experiments(self, experiment_ids: Sequence[str], *, split: str = "oos") -> pd.DataFrame:
        ids = [str(item) for item in experiment_ids]
        if not ids:
            return pd.DataFrame()
        placeholders = ",".join("?" for _ in ids)
        frame = self._db.fetch_frame(
            f"""SELECT experiment_id, metric_name, value FROM metrics
            WHERE split = ? AND step = 0 AND experiment_id IN ({placeholders})
            ORDER BY experiment_id, metric_name""",
            (split, *ids),
        )
        if frame.empty:
            return pd.DataFrame(index=pd.Index(ids, name="experiment_id"))
        result = frame.pivot(index="experiment_id", columns="metric_name", values="value")
        return result.reindex(ids)

    def list_models(self, *, limit: int = 200) -> pd.DataFrame:
        return self._db.fetch_frame(
            """SELECT model_id, family, artifact_uri, artifact_sha256, created_at
            FROM models ORDER BY created_at DESC, model_id LIMIT ?""",
            (int(limit),),
        )

    def list_factors(self, *, limit: int = 500) -> pd.DataFrame:
        return self._db.fetch_frame(
            """SELECT factor_id, name, definition_sha256, created_at
            FROM factors ORDER BY created_at DESC, factor_id LIMIT ?""",
            (int(limit),),
        )

    def list_portfolios(self, *, limit: int = 200) -> pd.DataFrame:
        return self._db.fetch_frame(
            """SELECT portfolio_id, experiment_id, asof_date, policy, artifact_uri,
            artifact_sha256, created_at FROM portfolios ORDER BY asof_date DESC, portfolio_id LIMIT ?""",
            (int(limit),),
        )
