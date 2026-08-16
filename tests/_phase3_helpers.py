from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from tushare_qlib.lineage import sha256_json
from tushare_qlib.prediction_snapshot import (
    PredictionSnapshotSpec,
    prediction_snapshot_path,
    write_prediction_snapshot,
)
from tushare_qlib.store import sha256_file


ANCHORS = {
    "P2-06": ("A4", "ridge"),
    "P2-07": ("A4", "xgboost"),
    "P2-08": ("A5", "xgboost"),
}


def phase3_entry_fixture(tmp_path: Path) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    contract_lock_sha = "phase2-contract-lock-test"
    candidates = [
        {
            "candidateId": f"H{number:03d}",
            "hypothesisId": f"H{number:03d}",
            "status": "REJECTED",
            "gatePass": False,
            "rejectionReasons": ["WORST_ROLLING_WINDOW"],
            "metrics": {},
            "alphaPack": "ashare_alpha_phase2_v1",
            "featureSet": "A4",
            "model": "ridge",
            "portfolio": "topk_dropout_v1",
            "regimeRule": "none",
        }
        for number in range(1, 12)
    ]
    acceptance = {
        "schemaVersion": "phase2_incremental_acceptance_v1",
        "programId": "ashare_alpha_research_phase2_v1",
        "contractLockSha256": contract_lock_sha,
        "candidates": candidates,
        "acceptedCount": 0,
        "selectionUsesFinalHoldout": False,
        "publishingAuthorized": False,
    }
    acceptance["acceptanceSha256"] = sha256_json(acceptance)
    acceptance_path = tmp_path / "phase2-acceptance.json"
    acceptance_path.write_text(json.dumps(acceptance, sort_keys=True), encoding="utf-8")

    release_identity = {
        "schemaVersion": "2.0",
        "profile": "ashare_qlib_research_v2",
        "requiredComponents": [],
        "components": [],
    }
    release_identity_sha = sha256_json(release_identity)
    release_id = f"ds_{release_identity_sha}"
    release = {
        **release_identity,
        "dataReleaseId": release_id,
        "identitySha256": release_identity_sha,
        "publishedAt": "2026-01-01T00:00:00Z",
    }
    release["manifestSha256"] = sha256_json(release)
    release_path = tmp_path / "release.json"
    release_path.write_text(json.dumps(release, sort_keys=True), encoding="utf-8")

    feature_root = tmp_path / "feature-snapshot"
    feature_root.mkdir()
    partition = feature_root / "year=2025.parquet"
    pd.DataFrame({"dummy": [1.0]}).to_parquet(partition)
    feature_identity = {
        "featureRecipeId": "fr_phase3_test",
        "coverage": {"startTime": "2025-01-01", "endTime": "2025-12-31"},
        "files": [{"name": partition.name, "sha256": sha256_file(partition), "rows": 1}],
    }
    feature_snapshot_id = "fs_" + sha256_json(feature_identity)
    feature_manifest = {
        "schemaVersion": "feature_snapshot_v1",
        **feature_identity,
        "featureSnapshotId": feature_snapshot_id,
        "contract": {
            "datasetId": release_id,
            "datasetVersionId": "dv_phase3_test",
        },
    }
    (feature_root / "manifest.json").write_text(
        json.dumps(feature_manifest, sort_keys=True), encoding="utf-8"
    )

    dates = pd.bdate_range("2025-01-02", periods=8)
    instruments = [f"SH{600000 + number:06d}" for number in range(8)]
    index = pd.MultiIndex.from_product([dates, instruments], names=["datetime", "instrument"])
    labels = pd.Series(np.tile(np.linspace(-1.0, 1.0, len(instruments)), len(dates)), index=index)
    labels_path = tmp_path / "labels.parquet"
    labels.rename("label").to_frame().to_parquet(labels_path)
    ablations: dict[str, object] = {}
    for position, (experiment_id, (feature_set, model)) in enumerate(ANCHORS.items(), start=1):
        scores = labels + position * 0.01
        payload_path = tmp_path / f"{experiment_id}.parquet"
        snapshot = write_prediction_snapshot(
            payload_path,
            scores.rename("score").to_frame(),
            labels=labels.rename("label").to_frame(),
            spec=PredictionSnapshotSpec(
                data_release_id=release_id,
                alpha_pack_id="ashare_alpha_phase2_v1",
                feature_snapshot_id=feature_snapshot_id,
                label_spec_id="return_5d_t1_v1",
                split_spec_id="wf_phase3_test",
                model_id=f"model_{experiment_id}",
                model_profile_id=f"{model}_profile_v1",
                fold_id="rolling_oos_aggregate",
                feature_set_id=feature_set,
            ),
        )
        snapshot_path = prediction_snapshot_path(payload_path)
        run = {
            "schemaVersion": "2.0",
            "runKind": "phase2_walk_forward",
            "dataset": {"datasetId": release_id, "versionId": "dv_phase3_test"},
            "featureStore": {
                "featureSnapshotId": feature_snapshot_id,
                "datasetVersionId": "dv_phase3_test",
            },
            "researchExperiment": {
                "experiment_id": f"exp_{experiment_id}",
                "feature_set_id": feature_set,
            },
            "runtime": {"modelFamily": model, "modelProfile": f"{model}_profile_v1"},
            "lineage": {
                "qlibPlatformCommit": "phase2-clean-commit",
                "qlibPlatformDirty": False,
                "complete": True,
            },
            "promotion": {"promotionAuthorized": False},
            "predictionSnapshot": snapshot,
            "folds": [
                {
                    "key": "rolling_01",
                    "train": ["2020-01-01", "2024-12-31"],
                    "test": [str(dates[0].date()), str(dates[3].date())],
                },
                {
                    "key": "rolling_02",
                    "train": ["2020-03-01", "2025-01-06"],
                    "test": [str(dates[4].date()), str(dates[-1].date())],
                },
            ],
            "artifacts": [{"name": "oos_predictions.snapshot.json", "localPath": str(snapshot_path)}],
        }
        run_path = tmp_path / f"run-{experiment_id}.json"
        run_path.write_text(json.dumps(run, sort_keys=True), encoding="utf-8")
        ablations[experiment_id] = {"runManifests": [str(run_path)]}

    evidence = {
        "schemaVersion": "phase2_evidence_index_v1",
        "contractLockSha256": contract_lock_sha,
        "dataReleaseManifest": str(release_path),
        "datasetVersionId": "dv_phase3_test",
        "featureSnapshot": str(feature_root),
        "labels": str(labels_path),
        "benchmarkFactorPanel": str(labels_path),
        "ablationExperiments": ablations,
        "candidates": [],
        "finalHoldout": False,
    }
    evidence_path = tmp_path / "phase2-evidence.json"
    evidence_path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
    return acceptance_path, evidence_path
