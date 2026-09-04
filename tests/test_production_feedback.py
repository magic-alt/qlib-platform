from __future__ import annotations

import json

import pandas as pd
import pytest

from qlib_platform.feedback.prediction_evaluation import (
    evaluate_prediction_snapshot,
    load_prediction_evaluation,
    prediction_evaluation_manifest_path,
)
from qlib_platform.feedback.realized_labels import (
    RealizedLabelSpec,
    load_realized_label_snapshot,
    realized_label_manifest_path,
    write_realized_label_snapshot,
)
from qlib_platform.artifacts.prediction_snapshot import PredictionSnapshotSpec, write_prediction_snapshot


CALENDAR = pd.date_range("2026-01-05", periods=6, freq="B")


def _index() -> pd.MultiIndex:
    return pd.MultiIndex.from_product(
        [[pd.Timestamp("2026-01-05")], ["A", "B", "C", "D"]],
        names=["datetime", "instrument"],
    )


def _realized_spec(*, data_release_id: str = "dr_test") -> RealizedLabelSpec:
    return RealizedLabelSpec(
        data_release_id=data_release_id,
        label_spec_id="return_1d_t1_v1",
        horizon_days=1,
        signal_lag_days=1,
        price_field="close",
        source_artifact_id="dr_test/bars/close",
    )


def _prediction_spec() -> PredictionSnapshotSpec:
    return PredictionSnapshotSpec(
        data_release_id="dr_test",
        alpha_pack_id="alpha_test",
        feature_snapshot_id="fs_test",
        label_spec_id="return_1d_t1_v1",
        split_spec_id="split_test",
        model_id="model_test",
        model_profile_id="profile_test",
        fold_id="live_20260105",
    )


def test_realized_label_snapshot_enforces_maturity_and_detects_tampering(tmp_path):
    labels = pd.DataFrame({"label": [0.04, 0.02, -0.01, -0.03]}, index=_index())
    payload = tmp_path / "realized.parquet"

    with pytest.raises(ValueError, match="not mature"):
        write_realized_label_snapshot(
            payload,
            labels,
            spec=_realized_spec(),
            trading_calendar=CALENDAR,
            observed_through="2026-01-06",
        )

    manifest = write_realized_label_snapshot(
        payload,
        labels,
        spec=_realized_spec(),
        trading_calendar=CALENDAR,
        observed_through="2026-01-07",
    )
    loaded, verified = load_realized_label_snapshot(payload)
    assert verified == manifest
    assert manifest["snapshotId"].startswith("rls_")
    pd.testing.assert_frame_equal(loaded, labels)

    sidecar = realized_label_manifest_path(payload)
    drifted = json.loads(sidecar.read_text(encoding="utf-8"))
    drifted["contract"]["data_release_id"] = "dr_other"
    sidecar.write_text(json.dumps(drifted), encoding="utf-8")
    with pytest.raises(ValueError, match="identity mismatch"):
        load_realized_label_snapshot(sidecar)


def test_prediction_evaluation_binds_inputs_and_computes_monitoring_metrics(tmp_path):
    index = _index()
    prediction_path = tmp_path / "predictions.parquet"
    realized_path = tmp_path / "realized.parquet"
    write_prediction_snapshot(
        prediction_path,
        pd.DataFrame({"score": [4.0, 3.0, 2.0, 1.0]}, index=index),
        spec=_prediction_spec(),
    )
    write_realized_label_snapshot(
        realized_path,
        pd.DataFrame({"label": [0.04, 0.02, -0.01, -0.03]}, index=index),
        spec=_realized_spec(),
        trading_calendar=CALENDAR,
        observed_through="2026-01-07",
    )

    output = tmp_path / "evaluation.parquet"
    manifest = evaluate_prediction_snapshot(
        output,
        prediction_snapshot=prediction_path,
        realized_label_snapshot=realized_path,
        topk=1,
        min_cross_section=4,
        rolling_window=20,
    )
    daily, verified = load_prediction_evaluation(output)

    assert verified == manifest
    assert manifest["decision"] == {"status": "PASS", "reasons": []}
    assert manifest["contract"]["dataReleaseId"] == "dr_test"
    assert manifest["summary"]["meanRankIc"] == pytest.approx(1.0)
    assert daily.iloc[0]["top_bottom_spread"] == pytest.approx(0.07)

    sidecar = prediction_evaluation_manifest_path(output)
    drifted = json.loads(sidecar.read_text(encoding="utf-8"))
    drifted["contract"]["predictionSnapshotId"] = "ps_other"
    sidecar.write_text(json.dumps(drifted), encoding="utf-8")
    with pytest.raises(ValueError, match="identity mismatch"):
        load_prediction_evaluation(sidecar)


def test_prediction_evaluation_rejects_release_binding_drift(tmp_path):
    index = _index()
    prediction_path = tmp_path / "predictions.parquet"
    realized_path = tmp_path / "realized.parquet"
    write_prediction_snapshot(
        prediction_path,
        pd.DataFrame({"score": [4.0, 3.0, 2.0, 1.0]}, index=index),
        spec=_prediction_spec(),
    )
    write_realized_label_snapshot(
        realized_path,
        pd.DataFrame({"label": [0.04, 0.02, -0.01, -0.03]}, index=index),
        spec=_realized_spec(data_release_id="dr_other"),
        trading_calendar=CALENDAR,
        observed_through="2026-01-07",
    )

    with pytest.raises(ValueError, match="data_release_id binding mismatch"):
        evaluate_prediction_snapshot(
            tmp_path / "evaluation.parquet",
            prediction_snapshot=prediction_path,
            realized_label_snapshot=realized_path,
            min_cross_section=4,
        )
