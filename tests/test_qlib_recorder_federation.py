from __future__ import annotations

import json
from pathlib import Path

from qlib_platform.qlib_compat.federation import federate_qlib_recorder
from qlib_platform.research.evidence.experiment_db import SCHEMA_VERSION
from qlib_platform.research.evidence.experiment_store import ExperimentStore


class _FakeRecorder:
    id = "run-123"
    experiment_id = "exp-456"
    name = "native-qrun"
    status = "FINISHED"
    uri = "file:/tmp/mlruns"
    artifact_uri = "file:/tmp/mlruns/artifacts"
    info = {
        "id": id,
        "experiment_id": experiment_id,
        "name": name,
        "status": status,
        "start_time": "2026-09-05 10:00:00",
        "end_time": "2026-09-05 10:05:00",
    }

    def list_params(self) -> dict[str, object]:
        return {"model": "FutureModel", "seed": 7}

    def list_tags(self) -> dict[str, object]:
        return {"source": "qrun"}

    def list_metrics(self) -> dict[str, object]:
        return {"IC": 0.12, "Rank IC": "0.21", "note": "not-numeric"}


def test_schema_v2_federates_qlib_recorder_metadata_without_copying_artifacts(tmp_path: Path) -> None:
    assert SCHEMA_VERSION == 2
    database = tmp_path / "experiments.duckdb"

    with ExperimentStore(database) as store:
        experiment_id = federate_qlib_recorder(store, _FakeRecorder())
        experiment = store.get_experiment(experiment_id)
        recorders = store.list_qlib_recorders()

        assert experiment is not None
        assert experiment_id == "qlib:exp-456:run-123"
        assert experiment["status"] == "FINISHED"
        assert experiment["lineage"] == {
            "source": "qlib-recorder",
            "qlibExperimentId": "exp-456",
            "qlibRecorderId": "run-123",
        }
        assert {row["metric_name"] for row in experiment["metrics"]} == {"IC", "Rank IC"}

        assert len(recorders) == 1
        row = recorders.iloc[0]
        assert row["recorder_id"] == "run-123"
        assert row["qlib_experiment_id"] == "exp-456"
        assert row["tracking_uri"] == "file:/tmp/mlruns"
        assert row["artifact_uri"] == "file:/tmp/mlruns/artifacts"

        raw = store._db.fetch_frame(
            "SELECT metrics_json FROM qlib_recorders WHERE experiment_id = ?", (experiment_id,)
        )
        metrics = json.loads(str(raw.iloc[0]["metrics_json"]))
        assert metrics["note"] == "not-numeric"
