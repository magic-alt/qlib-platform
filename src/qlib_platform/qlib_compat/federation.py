from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from qlib_platform.research.evidence.experiment_store import ExperimentStore


def _mapping(value: object) -> dict[str, Any]:
    return {str(key): item for key, item in value.items()} if isinstance(value, Mapping) else {}


def _recorder_info(recorder: Any) -> dict[str, Any]:
    info = getattr(recorder, "info", {})
    return _mapping(info)


def federate_qlib_recorder(
    store: ExperimentStore,
    recorder: Any,
    *,
    platform_experiment_id: str | None = None,
) -> str:
    """Index a Qlib Recorder in ExperimentStore without copying its artifacts."""

    info = _recorder_info(recorder)
    recorder_id = str(info.get("id") or getattr(recorder, "id", "")).strip()
    qlib_experiment_id = str(info.get("experiment_id") or getattr(recorder, "experiment_id", "")).strip()
    if not recorder_id:
        raise ValueError("Qlib Recorder has no recorder id")
    if not qlib_experiment_id:
        raise ValueError("Qlib Recorder has no experiment id")

    experiment_id = platform_experiment_id or f"qlib:{qlib_experiment_id}:{recorder_id}"
    params = _mapping(recorder.list_params()) if hasattr(recorder, "list_params") else {}
    tags = _mapping(recorder.list_tags()) if hasattr(recorder, "list_tags") else {}
    metrics = _mapping(recorder.list_metrics()) if hasattr(recorder, "list_metrics") else {}
    numeric_metrics: dict[str, float] = {}
    for name, value in metrics.items():
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            numeric_metrics[str(name)] = numeric

    status = str(info.get("status") or getattr(recorder, "status", "UNKNOWN"))
    store.register_experiment(
        experiment_id,
        status=status,
        params=params,
        lineage={
            "source": "qlib-recorder",
            "qlibExperimentId": qlib_experiment_id,
            "qlibRecorderId": recorder_id,
        },
        created_at=str(info.get("start_time")) if info.get("start_time") else None,
    )
    if numeric_metrics:
        store.log_metrics(experiment_id, numeric_metrics, split="qlib", step=0)
    store.register_qlib_recorder(
        experiment_id,
        recorder_id=recorder_id,
        qlib_experiment_id=qlib_experiment_id,
        recorder_name=str(info.get("name") or getattr(recorder, "name", "")) or None,
        status=status,
        tracking_uri=str(getattr(recorder, "uri", "")) or None,
        artifact_uri=str(getattr(recorder, "artifact_uri", "")) or None,
        start_time=str(info.get("start_time")) if info.get("start_time") else None,
        end_time=str(info.get("end_time")) if info.get("end_time") else None,
        params=params,
        tags=tags,
        metrics=metrics,
    )
    return experiment_id
