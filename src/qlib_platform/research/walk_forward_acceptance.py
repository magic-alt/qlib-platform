from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import pandas as pd


def _sessions(calendar: pd.DatetimeIndex, window: Sequence[str]) -> pd.DatetimeIndex:
    return calendar[(calendar >= pd.Timestamp(window[0])) & (calendar <= pd.Timestamp(window[1]))]


def validate_fold_integrity(
    folds: Iterable[object],
    calendar: pd.DatetimeIndex,
    *,
    label_lookahead_sessions: int,
) -> dict[str, Any]:
    """Prove fold chronology and label availability on the governed trading calendar."""

    governed = pd.DatetimeIndex(calendar).normalize().drop_duplicates().sort_values()
    if governed.empty:
        raise ValueError("walk-forward integrity requires a governed trading calendar")
    positions = {value: position for position, value in enumerate(governed)}
    fold_reports: list[dict[str, Any]] = []
    test_owners: dict[pd.Timestamp, str] = {}
    overlap_dates: list[str] = []
    leakage_sessions: list[dict[str, str]] = []
    previous_test_end: pd.Timestamp | None = None
    for fold in folds:
        key = str(getattr(fold, "key"))
        train = tuple(getattr(fold, "train"))
        valid = tuple(getattr(fold, "valid"))
        test = tuple(getattr(fold, "test"))
        train_sessions = _sessions(governed, train)
        valid_sessions = _sessions(governed, valid)
        test_sessions = _sessions(governed, test)
        if min(len(train_sessions), len(valid_sessions), len(test_sessions)) == 0:
            raise ValueError(f"fold {key} contains an empty governed session window")
        if not train_sessions.max() < valid_sessions.min() or not valid_sessions.max() < test_sessions.min():
            raise ValueError(f"fold {key} windows are not strictly chronological")
        intersections = {
            "trainValid": len(train_sessions.intersection(valid_sessions)),
            "trainTest": len(train_sessions.intersection(test_sessions)),
            "validTest": len(valid_sessions.intersection(test_sessions)),
        }
        if any(intersections.values()):
            raise ValueError(f"fold {key} governed windows overlap: {intersections}")
        purge_sessions = positions[valid_sessions.min()] - positions[train_sessions.max()] - 1
        embargo_sessions = positions[test_sessions.min()] - positions[valid_sessions.max()] - 1
        if min(purge_sessions, embargo_sessions) < label_lookahead_sessions:
            raise ValueError(
                f"fold {key} has an insufficient label gap: "
                f"purge={purge_sessions}, embargo={embargo_sessions}, "
                f"lookahead={label_lookahead_sessions}"
            )
        information_position = positions[train_sessions.max()] + label_lookahead_sessions
        if information_position >= len(governed):
            raise ValueError(f"fold {key} train labels extend beyond the governed calendar")
        information_date = governed[information_position]
        if information_date >= valid_sessions.min():
            leakage_sessions.append(
                {
                    "foldId": key,
                    "trainEnd": str(train_sessions.max().date()),
                    "labelInformationDate": str(information_date.date()),
                    "firstValidationDecisionDate": str(valid_sessions.min().date()),
                }
            )
        if previous_test_end is not None and test_sessions.min() <= previous_test_end:
            raise ValueError(f"fold {key} test window overlaps or is out of order")
        previous_test_end = test_sessions.max()
        for date in test_sessions:
            owner = test_owners.setdefault(date, key)
            if owner != key:
                overlap_dates.append(str(date.date()))
        fold_reports.append(
            {
                "foldId": key,
                "trainSessions": len(train_sessions),
                "validSessions": len(valid_sessions),
                "testSessions": len(test_sessions),
                "purgeSessions": purge_sessions,
                "embargoSessions": embargo_sessions,
                "labelLookaheadSessions": label_lookahead_sessions,
                "maxTrainLabelInformationDate": str(information_date.date()),
                "firstValidationDecisionDate": str(valid_sessions.min().date()),
                "firstTestDecisionDate": str(test_sessions.min().date()),
                "temporalLeakageRows": 0,
                "intersections": intersections,
            }
        )
    if leakage_sessions:
        raise ValueError(f"walk-forward temporal leakage detected: {leakage_sessions[:5]}")
    if overlap_dates:
        raise ValueError(f"walk-forward test dates overlap: {sorted(set(overlap_dates))[:5]}")
    return {
        "passed": True,
        "foldCount": len(fold_reports),
        "temporalLeakageRows": 0,
        "overlapDates": 0,
        "folds": fold_reports,
    }


def validate_processor_isolation(manifests: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not manifests:
        raise ValueError("processor isolation requires fold manifests")
    feature_ids: list[str] = []
    state_ids: list[str] = []
    model_ids: list[str] = []
    folds: list[dict[str, object]] = []
    for manifest in manifests:
        feature = manifest.get("featureStore")
        state = manifest.get("processorState")
        segments = manifest.get("folds")
        if not isinstance(feature, Mapping) or not str(feature.get("featureSnapshotId") or ""):
            raise ValueError("fold manifest is missing FeatureSnapshot identity")
        if not isinstance(state, Mapping) or not str(state.get("processorStateSha256") or ""):
            raise ValueError("fold manifest is missing processor-state identity")
        if not isinstance(segments, list) or len(segments) != 1 or not isinstance(segments[0], Mapping):
            raise ValueError("fold manifest is missing its split evidence")
        train = [str(value) for value in segments[0].get("train", [])]
        fit_window = [str(value) for value in state.get("fitWindow", [])]
        if fit_window != train:
            raise ValueError(
                f"processor fit window does not match fold train window: {fit_window} != {train}"
            )
        feature_ids.append(str(feature["featureSnapshotId"]))
        state_ids.append(str(state["processorStateSha256"]))
        model_ids.append(str(manifest.get("externalRunId") or ""))
        folds.append(
            {
                "runId": model_ids[-1],
                "featureSnapshotId": feature_ids[-1],
                "processorStateSha256": state_ids[-1],
                "fitWindow": fit_window,
            }
        )
    if len(set(feature_ids)) != 1:
        raise ValueError("FeatureSnapshot drift detected across walk-forward folds")
    if len(set(state_ids)) != len(state_ids):
        raise ValueError("fitted processor state was reused across walk-forward folds")
    if len(set(model_ids)) != len(model_ids) or any(not value for value in model_ids):
        raise ValueError("model identity was reused across walk-forward folds")
    return {
        "passed": True,
        "featureSnapshotId": feature_ids[0],
        "featureSnapshotShared": True,
        "freshProcessorStateEachFold": True,
        "freshModelEachFold": True,
        "processorStateSha256UniqueCount": len(set(state_ids)),
        "folds": folds,
    }


def performance_baseline(
    component_runs: Sequence[Mapping[str, Any]],
    *,
    feature_store: Mapping[str, Any] | None,
    feature_seconds: float,
    portfolio_manifest: Mapping[str, Any],
    orchestration_seconds: float,
) -> dict[str, Any]:
    fold_rows: list[dict[str, object]] = []
    peak_values: list[float] = []
    for run in component_runs:
        timings = run.get("timings", {})
        timings = timings if isinstance(timings, Mapping) else {}
        phases = timings.get("phasesSeconds", {})
        phases = phases if isinstance(phases, Mapping) else {}
        peak = timings.get("peakRssMb")
        if isinstance(peak, (int, float)) and math.isfinite(float(peak)):
            peak_values.append(float(peak))
        fold_rows.append(
            {
                "foldId": str(run.get("key") or ""),
                "checkpointStatus": str(run.get("checkpointStatus") or ""),
                "datasetSeconds": round(
                    float(phases.get("dataset_prepare_seconds", 0.0))
                    + float(phases.get("handler_process_seconds", 0.0)),
                    6,
                ),
                "trainSeconds": round(float(phases.get("train_seconds", 0.0)), 6),
                "predictSeconds": round(float(phases.get("predict_seconds", 0.0)), 6),
                "peakRssMb": peak,
                "totalSeconds": timings.get("totalSeconds"),
            }
        )
    portfolio_timings = portfolio_manifest.get("timings", {})
    portfolio_timings = portfolio_timings if isinstance(portfolio_timings, Mapping) else {}
    portfolio_phases = portfolio_timings.get("phasesSeconds", {})
    portfolio_phases = portfolio_phases if isinstance(portfolio_phases, Mapping) else {}
    portfolio_peak = portfolio_timings.get("peakRssMb")
    if isinstance(portfolio_peak, (int, float)) and math.isfinite(float(portfolio_peak)):
        peak_values.append(float(portfolio_peak))
    return {
        "status": "BASELINE_RECORDED",
        "feature": {
            "snapshotStatus": (feature_store or {}).get("cacheStatus"),
            "rawMaterializationCalls": (feature_store or {}).get("rawMaterializationCalls"),
            "seconds": round(feature_seconds, 6),
        },
        "folds": fold_rows,
        "portfolioSeconds": round(float(portfolio_phases.get("portfolio_engine_seconds", 0.0)), 6),
        "totalSeconds": round(orchestration_seconds, 6),
        "peakRssMb": max(peak_values) if peak_values else None,
    }
