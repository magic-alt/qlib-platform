from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tushare_qlib.cli import parser
from tushare_qlib.lineage import sha256_json
from tushare_qlib.prediction_snapshot import PredictionSnapshotSpec, write_prediction_snapshot
from tushare_qlib.research.feature_diagnostics import FeatureDiagnosticsSpec
from tushare_qlib.research.study import run_alpha_diagnose
from tushare_qlib.settings import Paths, Settings
from tushare_qlib.store import sha256_file


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={},
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    dates = pd.bdate_range("2025-01-02", periods=4)
    instruments = [f"S{number:02d}" for number in range(10)]
    index = pd.MultiIndex.from_product([dates, instruments], names=["datetime", "instrument"])
    values = np.tile(np.arange(10, dtype=float), len(dates))
    labels = pd.DataFrame({"label": values}, index=index)

    feature_root = tmp_path / "feature" / "fs-test"
    feature_root.mkdir(parents=True)
    partition = feature_root / "year=2025.parquet"
    pd.DataFrame({"POS": values, "NEG": -values}, index=index).to_parquet(partition)
    feature_manifest = {
        "schemaVersion": "feature_snapshot_v1",
        "featureRecipeId": "fr-test",
        "featureSnapshotId": "fs-test",
        "contract": {
            "datasetVersionId": "dataset-version",
            "alphaPack": {
                "pack_id": "alpha158_pit_v1",
                "alpha_pack_sha256": "alpha-sha",
            },
        },
        "coverage": {"startTime": "2025-01-02", "endTime": "2025-01-07"},
        "files": [{"name": partition.name, "rows": len(index), "sha256": sha256_file(partition)}],
    }
    feature_manifest_path = feature_root / "manifest.json"
    feature_manifest_path.write_text(json.dumps(feature_manifest, sort_keys=True, indent=2), encoding="utf-8")

    run_root = tmp_path / "xgb-run"
    run_root.mkdir()
    prediction_path = run_root / "oos_predictions.parquet"
    write_prediction_snapshot(
        prediction_path,
        pd.DataFrame({"score": values}, index=index),
        labels=labels,
        spec=PredictionSnapshotSpec(
            data_release_id="ds-test",
            alpha_pack_id="alpha158_pit_v1",
            feature_snapshot_id="fs-test",
            label_spec_id="return_1d_t1_v1",
            split_spec_id="split-test",
            model_id="xgb-test",
            model_profile_id="xgb-profile",
            fold_id="rolling_oos_aggregate",
        ),
    )
    label_path = run_root / "oos_labels.parquet"
    labels.to_parquet(label_path)
    folds = [
        {
            "key": "rolling_00",
            "train": ["2024-01-01", "2024-06-30"],
            "valid": ["2024-07-01", "2024-07-31"],
            "test": ["2025-01-02", "2025-01-03"],
            "final_holdout": False,
        },
        {
            "key": "rolling_01",
            "train": ["2024-02-01", "2024-07-31"],
            "valid": ["2024-08-01", "2024-08-31"],
            "test": ["2025-01-06", "2025-01-07"],
            "final_holdout": False,
        },
        {
            "key": "final_holdout",
            "train": ["2024-03-01", "2024-08-31"],
            "valid": ["2024-09-01", "2024-09-30"],
            "test": ["2025-02-01", "2025-02-28"],
            "final_holdout": True,
        },
    ]
    selection_lock = {
        "schemaVersion": "research_selection_lock_v1",
        "dataRelease": "ds-test",
        "alphaPack": {"id": "alpha158_pit_v1", "sha256": "alpha-sha"},
        "labelSpec": {
            "id": "return_1d_t1_v1",
            "contract": {"lookahead_days": 2, "horizon_days": 1, "signal_lag_days": 1},
        },
        "splitSpec": {"profile": "test", "folds": folds, "sha256": "split-sha"},
        "codeCommit": "certified-commit",
        "codeDirty": False,
        "finalHoldout": {"usedForResearchSelection": False, "accessedBeforeFinalization": False},
    }
    selection_lock["lockSha256"] = sha256_json(selection_lock)
    evidence = {
        "systemAcceptance": "PASS",
        "walkForwardIntegrity": "PASS",
        "researchSelectionLock": selection_lock,
        "featureSnapshot": {
            "featureSnapshotId": "fs-test",
            "manifestSha256": sha256_file(feature_manifest_path),
            "datasetVersionId": "dataset-version",
            "rawMaterializationCalls": 0,
        },
        "oosPrediction": {"predictionDates": 4},
        "model": {"family": "xgboost", "profile": "xgb-profile"},
    }
    (run_root / "walk_forward_evidence.json").write_text(
        json.dumps(evidence, sort_keys=True), encoding="utf-8"
    )
    (run_root / "research_selection_lock.json").write_text(
        json.dumps(selection_lock, sort_keys=True), encoding="utf-8"
    )
    run_manifest = {
        "walkForwardEvidence": evidence,
        "folds": folds,
        "artifacts": [
            {"name": prediction_path.name, "localPath": str(prediction_path)},
            {
                "name": "oos_predictions.snapshot.json",
                "localPath": str(run_root / "oos_predictions.snapshot.json"),
            },
            {"name": label_path.name, "localPath": str(label_path)},
        ],
    }
    (run_root / "manifest.json").write_text(json.dumps(run_manifest, sort_keys=True), encoding="utf-8")

    acceptance_path = tmp_path / "full_walk_forward_acceptance.json"
    acceptance = {
        "acceptanceType": "FULL_WALK_FORWARD_V1",
        "systemAcceptance": "PASS",
        "walkForwardAcceptance": "PASS",
        "data": {"dataRelease": "ds-test"},
        "featureSnapshot": {
            "featureSnapshotId": "fs-test",
            "rawMaterializationCalls": 0,
        },
        "models": {
            "ridge": {"predictionSha256": "ridge-sha"},
            "lightgbm": {"predictionSha256": "lightgbm-sha"},
            "xgboost": {"predictionSha256": sha256_file(prediction_path)},
        },
        "determinism": {"ridge": "EXACT", "lightgbm": "EXACT", "xgboost": "EXACT"},
        "finalHoldout": {
            "isolated": True,
            "usedForResearchSelection": False,
            "accessedBeforeFinalization": False,
        },
    }
    acceptance_path.write_text(json.dumps(acceptance, sort_keys=True), encoding="utf-8")

    taxonomy_path = tmp_path / "taxonomy.yaml"
    taxonomy_path.write_text(
        """schema: alpha_factor_taxonomy_v1
taxonomyId: test_taxonomy
alphaPackId: alpha158_pit_v1
features:
  POS: {family: Momentum, role: alpha, direction: positive}
  NEG: {family: Value, role: alpha, direction: negative}
""",
        encoding="utf-8",
    )
    return acceptance_path, run_root, feature_root, taxonomy_path


def test_study_is_non_publishing_checksum_bound_and_exactly_deterministic(tmp_path: Path):
    acceptance, run, feature, taxonomy = _fixture(tmp_path)
    settings = _settings(tmp_path)
    spec = FeatureDiagnosticsSpec(
        min_cross_section=5,
        rolling_sessions=3,
        short_rolling_sessions=2,
        quantiles=5,
    )

    first = run_alpha_diagnose(
        settings,
        acceptance=acceptance,
        walk_forward=run,
        feature_snapshot=feature,
        taxonomy_path=taxonomy,
        output_root=tmp_path / "output-a",
        spec=spec,
    )
    second = run_alpha_diagnose(
        settings,
        acceptance=acceptance,
        walk_forward=run,
        feature_snapshot=feature,
        taxonomy_path=taxonomy,
        output_root=tmp_path / "output-b",
        spec=spec,
    )
    reused = run_alpha_diagnose(
        settings,
        acceptance=acceptance,
        walk_forward=run,
        feature_snapshot=feature,
        taxonomy_path=taxonomy,
        output_root=tmp_path / "output-a",
        spec=spec,
    )
    first_manifest = json.loads(first.read_text(encoding="utf-8"))
    second_manifest = json.loads(second.read_text(encoding="utf-8"))

    assert reused == first
    assert first_manifest["studyId"] == second_manifest["studyId"]
    assert first_manifest["rawMaterializationCalls"] == 0
    assert first_manifest["selectionUsesFinalHoldout"] is False
    assert first_manifest["publishingAuthorized"] is False
    assert first_manifest["rollingOosSessions"] == 4
    first_hashes = {item["name"]: item["sha256"] for item in first_manifest["artifacts"]}
    second_hashes = {item["name"]: item["sha256"] for item in second_manifest["artifacts"]}
    assert first_hashes == second_hashes
    assert not (run / "final_holdout_labels.parquet").exists()


def test_study_fails_closed_on_feature_partition_tamper(tmp_path: Path):
    acceptance, run, feature, taxonomy = _fixture(tmp_path)
    partition = feature / "year=2025.parquet"
    changed = pd.read_parquet(partition)
    changed.iloc[0, 0] = 999.0
    changed.to_parquet(partition)

    with pytest.raises(ValueError, match="checksum mismatch"):
        run_alpha_diagnose(
            _settings(tmp_path),
            acceptance=acceptance,
            walk_forward=run,
            feature_snapshot=feature,
            taxonomy_path=taxonomy,
            output_root=tmp_path / "output",
            spec=FeatureDiagnosticsSpec(
                min_cross_section=5,
                rolling_sessions=3,
                short_rolling_sessions=2,
            ),
        )


def test_study_fails_closed_on_data_release_drift(tmp_path: Path):
    acceptance, run, feature, taxonomy = _fixture(tmp_path)
    payload = json.loads(acceptance.read_text(encoding="utf-8"))
    payload["data"]["dataRelease"] = "ds-other"
    acceptance.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    with pytest.raises(ValueError, match="DataRelease mismatch"):
        run_alpha_diagnose(
            _settings(tmp_path),
            acceptance=acceptance,
            walk_forward=run,
            feature_snapshot=feature,
            taxonomy_path=taxonomy,
            output_root=tmp_path / "output",
        )


def test_alpha_diagnose_cli_requires_governed_inputs():
    args = parser().parse_args(
        [
            "alpha-diagnose",
            "--acceptance",
            "acceptance.json",
            "--walk-forward",
            "xgb-run",
            "--feature-snapshot",
            "fs-test",
        ]
    )

    assert args.command == "alpha-diagnose"
    assert args.taxonomy == "configs/alpha_taxonomy/alpha158_pit_v1.yaml"
