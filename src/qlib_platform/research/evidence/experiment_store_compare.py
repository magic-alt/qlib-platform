from __future__ import annotations

import json
from collections.abc import Sequence

import pandas as pd

from qlib_platform.research.evidence.experiment_db import Connection


class ExperimentCompareMixin:
    _db: Connection

    @staticmethod
    def _ids(values: Sequence[str]) -> tuple[list[str], str]:
        ids = [str(item) for item in values]
        return ids, ",".join("?" for _ in ids)

    def compare_models(self, model_ids: Sequence[str]) -> pd.DataFrame:
        ids, placeholders = self._ids(model_ids)
        if not ids:
            return pd.DataFrame()
        frame = self._db.fetch_frame(
            f"""SELECT model_id, family, config_json, artifact_uri, artifact_sha256
            FROM models WHERE model_id IN ({placeholders})""",
            ids,
        )
        if frame.empty:
            return frame
        frame["config"] = frame["config_json"].map(lambda value: json.loads(str(value)))
        return frame.drop(columns=["config_json"]).set_index("model_id").reindex(ids).reset_index()

    def compare_factors(self, factor_ids: Sequence[str]) -> pd.DataFrame:
        ids, placeholders = self._ids(factor_ids)
        if not ids:
            return pd.DataFrame()
        frame = self._db.fetch_frame(
            f"""SELECT factor_id, name, definition_sha256, metadata_json
            FROM factors WHERE factor_id IN ({placeholders})""",
            ids,
        )
        if frame.empty:
            return frame
        frame["metadata"] = frame["metadata_json"].map(lambda value: json.loads(str(value)))
        return frame.drop(columns=["metadata_json"]).set_index("factor_id").reindex(ids).reset_index()

    def compare_portfolios(self, portfolio_ids: Sequence[str]) -> pd.DataFrame:
        ids, placeholders = self._ids(portfolio_ids)
        if not ids:
            return pd.DataFrame()
        frame = self._db.fetch_frame(
            f"""SELECT portfolio_id, experiment_id, asof_date, policy, metrics_json, artifact_uri
            FROM portfolios WHERE portfolio_id IN ({placeholders})""",
            ids,
        )
        if frame.empty:
            return frame
        parsed = frame["metrics_json"].map(lambda value: json.loads(str(value)))
        keys = sorted({str(key) for payload in parsed for key in payload})
        for key in keys:
            frame[f"metric:{key}"] = parsed.map(lambda payload, name=key: payload.get(name))
        return frame.drop(columns=["metrics_json"]).set_index("portfolio_id").reindex(ids).reset_index()
