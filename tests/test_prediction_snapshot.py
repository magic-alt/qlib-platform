from __future__ import annotations

import json

import pandas as pd
import pytest

from qlib_platform.artifacts.prediction_snapshot import (
    PredictionSnapshotSpec,
    load_prediction_snapshot,
    prediction_snapshot_path,
    write_prediction_snapshot,
)


def _spec() -> PredictionSnapshotSpec:
    return PredictionSnapshotSpec(
        data_release_id="ds_test",
        alpha_pack_id="alpha158_pit_v1",
        feature_snapshot_id="fs_test",
        label_spec_id="return_5d_t1_v1",
        split_spec_id="split_test",
        model_id="model_test",
        model_profile_id="ridge_golden_v1",
        fold_id="fold_1",
    )


def _frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2026-01-05"), "SH600000"),
            (pd.Timestamp("2026-01-05"), "SZ000001"),
        ],
        names=["datetime", "instrument"],
    )
    return pd.DataFrame({"score": [0.25, -0.1]}, index=index), pd.DataFrame(
        {"LABEL0": [0.03, -0.02]}, index=index
    )


def test_prediction_snapshot_round_trip_binds_complete_research_contract(tmp_path):
    predictions, labels = _frames()
    path = tmp_path / "oos_predictions.parquet"

    written = write_prediction_snapshot(path, predictions, labels=labels, spec=_spec())
    loaded, manifest = load_prediction_snapshot(prediction_snapshot_path(path))

    assert written == manifest
    assert manifest["snapshotId"].startswith("ps_")
    assert manifest["contract"]["model_profile_id"] == "ridge_golden_v1"
    assert list(loaded.columns) == ["score", "label"]
    pd.testing.assert_series_equal(loaded["score"], predictions["score"])


def test_prediction_snapshot_rejects_duplicate_keys(tmp_path):
    predictions, labels = _frames()
    duplicated = pd.concat([predictions, predictions.iloc[[0]]])

    with pytest.raises(ValueError, match="duplicate datetime/instrument"):
        write_prediction_snapshot(tmp_path / "bad.parquet", duplicated, labels=labels, spec=_spec())


def test_prediction_snapshot_rejects_payload_drift(tmp_path):
    predictions, labels = _frames()
    path = tmp_path / "oos_predictions.parquet"
    write_prediction_snapshot(path, predictions, labels=labels, spec=_spec())
    drifted = predictions.copy()
    drifted.iloc[0, 0] = 999.0
    drifted.to_parquet(path)

    with pytest.raises(ValueError, match="payload checksum mismatch"):
        load_prediction_snapshot(path)


def test_prediction_snapshot_rejects_contract_drift(tmp_path):
    predictions, labels = _frames()
    path = tmp_path / "oos_predictions.parquet"
    write_prediction_snapshot(path, predictions, labels=labels, spec=_spec())
    sidecar = prediction_snapshot_path(path)
    manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    manifest["contract"]["model_id"] = "other"
    sidecar.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="identity mismatch"):
        load_prediction_snapshot(sidecar)
