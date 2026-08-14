from __future__ import annotations

import json

import pandas as pd
import pytest

from tushare_qlib.feature_store import load_feature_store
from tushare_qlib.prediction_snapshot import (
    PredictionSnapshotSpec,
    load_prediction_snapshot,
    write_prediction_snapshot,
)
from tushare_qlib.settings import Paths, Settings
from tushare_qlib.store import sha256_file
from tushare_qlib.walk_forward import _validated_checkpoint_manifest


def test_corrupt_feature_partition_fails_before_training(tmp_path):
    root = tmp_path / "feature-snapshot"
    root.mkdir()
    partition = root / "year=2026.parquet"
    pd.DataFrame({"feature": [1.0]}).to_parquet(partition)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schemaVersion": "feature_snapshot_v1",
                "files": [{"name": partition.name, "sha256": sha256_file(partition)}],
            }
        ),
        encoding="utf-8",
    )
    partition.write_bytes(b"corrupt")

    with pytest.raises(ValueError, match="partition checksum mismatch"):
        load_feature_store(root, "2026-01-01", "2026-12-31", verify_checksums=True)


def test_interrupted_checkpoint_is_never_reused(tmp_path):
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={},
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )
    checkpoint = tmp_path / "rolling_00.json"
    checkpoint.write_text('{"checkpointFingerprint": "expected", "manifest": ', encoding="utf-8")

    assert _validated_checkpoint_manifest(settings, checkpoint, "expected") is None


def test_prediction_payload_tamper_fails_before_portfolio_replay(tmp_path):
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-01-05"), "SH600000")],
        names=["datetime", "instrument"],
    )
    path = tmp_path / "oos_predictions.parquet"
    write_prediction_snapshot(
        path,
        pd.DataFrame({"score": [0.25]}, index=index),
        spec=PredictionSnapshotSpec(
            data_release_id="ds_test",
            alpha_pack_id="alpha158_pit_v1",
            feature_snapshot_id="fs_test",
            label_spec_id="return_5d_t1_v1",
            split_spec_id="split_test",
            model_id="model_test",
            model_profile_id="ridge_golden_v1",
            fold_id="rolling_00",
        ),
    )
    pd.DataFrame({"score": [999.0]}, index=index).to_parquet(path)

    with pytest.raises(ValueError, match="payload checksum mismatch"):
        load_prediction_snapshot(path)
