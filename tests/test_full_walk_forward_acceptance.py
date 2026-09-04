from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from qlib_platform.full_walk_forward_acceptance import build_full_walk_forward_acceptance
from qlib_platform.lineage import sha256_json
from qlib_platform.processor_state import processor_state_manifest
from qlib_platform.settings import Paths, Settings
from qlib_platform.walk_forward import (
    Fold,
    _checkpoint_payload,
    _inspect_checkpoint,
    _verify_fold_boundary_continuity,
    _write_continuous_oos_stream,
)
from qlib_platform.walk_forward_acceptance import (
    validate_fold_integrity,
    validate_processor_isolation,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={},
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )


def test_fold_integrity_uses_governed_sessions_and_label_information_date():
    calendar = pd.bdate_range("2025-01-01", periods=30)
    folds = [
        Fold(
            "rolling_00",
            (str(calendar[0].date()), str(calendar[4].date())),
            (str(calendar[7].date()), str(calendar[9].date())),
            (str(calendar[12].date()), str(calendar[14].date())),
        ),
        Fold(
            "final_holdout",
            (str(calendar[3].date()), str(calendar[7].date())),
            (str(calendar[10].date()), str(calendar[12].date())),
            (str(calendar[15].date()), str(calendar[17].date())),
            True,
        ),
    ]

    result = validate_fold_integrity(folds, calendar, label_lookahead_sessions=2)

    assert result["passed"] is True
    assert result["temporalLeakageRows"] == 0
    assert result["folds"][0]["purgeSessions"] == 2
    assert result["folds"][0]["maxTrainLabelInformationDate"] == str(calendar[6].date())


def test_fold_integrity_rejects_insufficient_gap_and_holdout_overlap():
    calendar = pd.bdate_range("2025-01-01", periods=30)
    insufficient = Fold(
        "rolling_00",
        (str(calendar[0].date()), str(calendar[4].date())),
        (str(calendar[6].date()), str(calendar[8].date())),
        (str(calendar[11].date()), str(calendar[13].date())),
    )
    with pytest.raises(ValueError, match="insufficient label gap"):
        validate_fold_integrity([insufficient], calendar, label_lookahead_sessions=2)

    overlapping = Fold(
        "final_holdout",
        (str(calendar[0].date()), str(calendar[4].date())),
        (str(calendar[7].date()), str(calendar[9].date())),
        (str(calendar[13].date()), str(calendar[15].date())),
        True,
    )
    first = Fold(
        "rolling_00",
        (str(calendar[0].date()), str(calendar[4].date())),
        (str(calendar[7].date()), str(calendar[9].date())),
        (str(calendar[12].date()), str(calendar[14].date())),
    )
    with pytest.raises(ValueError, match="overlaps or is out of order"):
        validate_fold_integrity([first, overlapping], calendar, label_lookahead_sessions=2)


def test_processor_state_is_fitted_per_fold_but_feature_snapshot_is_shared():
    handler = SimpleNamespace(
        shared_processors=[],
        infer_processors=[SimpleNamespace(center=pd.Series([1.0, 2.0]))],
        learn_processors=[],
    )
    first_state = processor_state_manifest(handler, ("2024-01-01", "2024-12-31"))
    second_state = processor_state_manifest(handler, ("2024-02-01", "2025-01-31"))
    manifests = []
    for number, state in enumerate((first_state, second_state), start=1):
        manifests.append(
            {
                "externalRunId": f"run-{number}",
                "featureStore": {"featureSnapshotId": "fs-shared"},
                "processorState": state,
                "folds": [{"train": state["fitWindow"]}],
            }
        )

    result = validate_processor_isolation(manifests)

    assert result["featureSnapshotShared"] is True
    assert result["processorStateSha256UniqueCount"] == 2


def test_checkpoint_payload_tamper_is_invalidated(tmp_path: Path):
    artifact = tmp_path / "oos_predictions.parquet"
    pd.DataFrame({"score": [1.0]}).to_parquet(artifact)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "dataset": {"fingerprint": "dataset"},
                "lineage": {"lineageId": "lineage", "complete": True},
                "artifacts": [{"name": artifact.name, "localPath": str(artifact)}],
            }
        ),
        encoding="utf-8",
    )
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps(_checkpoint_payload(manifest_path, "expected")), encoding="utf-8")

    assert _inspect_checkpoint(_settings(tmp_path), checkpoint, "expected").status == "VALID"
    pd.DataFrame({"score": [999.0]}).to_parquet(artifact)
    inspected = _inspect_checkpoint(_settings(tmp_path), checkpoint, "expected")
    assert inspected.status == "CORRUPTED"
    assert inspected.reason == "artifact_sha256:oos_predictions.parquet"


def test_checkpoint_directory_artifact_tamper_is_invalidated(tmp_path: Path):
    artifact = tmp_path / "report_assets"
    artifact.mkdir()
    chart = artifact / "equity_curve.svg"
    chart.write_text("version-1", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "dataset": {"fingerprint": "dataset"},
                "lineage": {"lineageId": "lineage", "complete": True},
                "artifacts": [{"name": artifact.name, "localPath": str(artifact)}],
            }
        ),
        encoding="utf-8",
    )
    checkpoint = tmp_path / "checkpoint.json"
    payload = _checkpoint_payload(manifest_path, "expected")
    checkpoint.write_text(json.dumps(payload), encoding="utf-8")

    assert payload["artifacts"][0]["kind"] == "directory"
    assert _inspect_checkpoint(_settings(tmp_path), checkpoint, "expected").status == "VALID"
    chart.write_text("version-2", encoding="utf-8")
    inspected = _inspect_checkpoint(_settings(tmp_path), checkpoint, "expected")
    assert inspected.status == "CORRUPTED"
    assert inspected.reason == "artifact_sha256:report_assets"


def test_continuous_oos_rejects_missing_dates_and_out_of_order_rows(tmp_path: Path):
    output = tmp_path / "fold"
    output.mkdir()
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2025-01-07"), "A"), (pd.Timestamp("2025-01-06"), "A")],
        names=["datetime", "instrument"],
    )
    pred = output / "oos_predictions.parquet"
    label = output / "oos_labels.parquet"
    pd.DataFrame({"score": [1.0, 2.0]}, index=index).to_parquet(pred)
    pd.DataFrame({"label": [0.1, 0.2]}, index=index).to_parquet(label)
    manifest = {
        "externalRunId": "run",
        "artifacts": [
            {"name": pred.name, "localPath": str(pred)},
            {"name": label.name, "localPath": str(label)},
        ],
    }
    with pytest.raises(ValueError, match="out-of-order"):
        _write_continuous_oos_stream([manifest], tmp_path / "aggregate")

    ordered = index.sort_values()
    pd.DataFrame({"score": [1.0, 2.0]}, index=ordered).to_parquet(pred)
    pd.DataFrame({"label": [0.1, 0.2]}, index=ordered).to_parquet(label)
    with pytest.raises(ValueError, match="calendar mismatch"):
        _write_continuous_oos_stream(
            [manifest],
            tmp_path / "aggregate",
            expected_dates=pd.to_datetime(["2025-01-06", "2025-01-07", "2025-01-08"]),
        )


def test_continuous_oos_rejects_duplicate_prediction_key(tmp_path: Path):
    output = tmp_path / "fold"
    output.mkdir()
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2025-01-06"), "A"), (pd.Timestamp("2025-01-06"), "A")],
        names=["datetime", "instrument"],
    )
    pred = output / "oos_predictions.parquet"
    label = output / "oos_labels.parquet"
    pd.DataFrame({"score": [1.0, 2.0]}, index=index).to_parquet(pred)
    pd.DataFrame({"label": [0.1, 0.2]}, index=index).to_parquet(label)
    manifest = {
        "externalRunId": "run",
        "artifacts": [
            {"name": pred.name, "localPath": str(pred)},
            {"name": label.name, "localPath": str(label)},
        ],
    }

    with pytest.raises(ValueError, match="duplicate datetime/instrument"):
        _write_continuous_oos_stream([manifest], tmp_path / "aggregate")


def test_fold_boundary_cash_reset_fails_closed(tmp_path: Path):
    manifests = []
    for run_id, date in (("run-1", "2025-01-06"), ("run-2", "2025-01-07")):
        folder = tmp_path / run_id
        folder.mkdir()
        path = folder / "oos_predictions.parquet"
        index = pd.MultiIndex.from_tuples([(pd.Timestamp(date), "A")], names=["datetime", "instrument"])
        pd.DataFrame({"score": [1.0]}, index=index).to_parquet(path)
        manifests.append(
            {"externalRunId": run_id, "artifacts": [{"name": path.name, "localPath": str(path)}]}
        )
    holdings = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2025-01-06", "2025-01-07"]),
            "instrument": ["A", "A"],
            "quantity": [100.0, 100.0],
            "holding_days": [3, 4],
        }
    )
    report = pd.DataFrame(
        {
            "account": [101_000.0, 100_000.0],
            "cash": [10_000.0, 100_000.0],
            "total_turnover": [50_000.0, 0.0],
            "total_cost": [20.0, 0.0],
        },
        index=pd.to_datetime(["2025-01-06", "2025-01-07"]),
    )
    with pytest.raises(RuntimeError, match="cash/account reset"):
        _verify_fold_boundary_continuity(manifests, holdings, pd.DataFrame(), report, initial_cash=100_000.0)


def _bundle(
    root: Path,
    *,
    profile: str,
    family: str,
    score: float,
    reuse: int = 0,
    invalidated: int = 0,
    copy_from: Path | None = None,
) -> Path:
    root.mkdir(parents=True)
    names = (
        "oos_predictions.parquet",
        "oos_labels.parquet",
        "portfolio_report.parquet",
        "holdings.parquet",
        "final_holdout_predictions.parquet",
        "final_holdout_labels.parquet",
        "final_holdout_portfolio_report.parquet",
        "final_holdout_holdings.parquet",
    )
    if copy_from is not None:
        for name in names:
            shutil.copy2(copy_from / name, root / name)
    else:
        pd.DataFrame({"score": [score]}).to_parquet(root / names[0])
        pd.DataFrame({"label": [0.1]}).to_parquet(root / names[1])
        pd.DataFrame({"account": [100_000.0]}).to_parquet(root / names[2])
        pd.DataFrame({"instrument": ["A"], "quantity": [100.0]}).to_parquet(root / names[3])
        pd.DataFrame({"score": [score + 0.5]}).to_parquet(root / names[4])
        pd.DataFrame({"label": [0.2]}).to_parquet(root / names[5])
        pd.DataFrame({"account": [101_000.0]}).to_parquet(root / names[6])
        pd.DataFrame({"instrument": ["B"], "quantity": [100.0]}).to_parquet(root / names[7])
    selection_lock = {
        "dataRelease": "ds-test",
        "alphaPack": {"id": "alpha", "sha256": "alpha-sha"},
        "labelSpec": {"id": "label", "contract": {"lookahead": 6}},
        "splitSpec": {"profile": "wf", "folds": [1, 2], "sha256": "split-sha"},
        "portfolioPolicy": {"id": "topk", "sha256": "portfolio-sha"},
        "gateThresholds": {"min": 1},
        "codeCommit": "commit",
        "codeDirty": False,
    }
    selection_lock["lockSha256"] = sha256_json(selection_lock)
    evidence = {
        "systemAcceptance": "PASS",
        "walkForwardIntegrity": "PASS",
        "researchQuality": "REJECT",
        "researchSelectionLock": selection_lock,
        "featureSnapshot": {"featureSnapshotId": "fs-test", "rawMaterializationCalls": 0},
        "oosPrediction": {"startDate": "2025-01-01", "endDate": "2025-12-31", "predictionDates": 252},
        "foldIntegrity": {"passed": True, "temporalLeakageRows": 0},
        "stateContinuity": {
            "boundaryHoldingResetCount": 0,
            "boundaryCashResetCount": 0,
            "portfolioInitialCashEventCount": 1,
        },
        "checkpointRecovery": {
            "validFoldReuseCount": reuse,
            "invalidatedAndRebuiltCount": invalidated,
        },
        "finalHoldout": {"isolated": True},
        "model": {"profile": profile, "family": family},
        "performance": {"status": "BASELINE_RECORDED"},
        "researchStability": {"folds": []},
    }
    (root / "walk_forward_evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
    (root / "research_selection_lock.json").write_text(json.dumps(selection_lock), encoding="utf-8")
    manifest = {
        "folds": [{"key": "rolling_00"}],
        "walkForwardEvidence": evidence,
        "artifacts": [{"name": name, "localPath": str(root / name)} for name in names],
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_full_acceptance_requires_three_exact_resumes_and_corruption_rebuild(tmp_path: Path):
    runs: dict[str, tuple[Path, Path]] = {}
    profiles = {
        "ridge": ("ridge_golden_v1", "ridge", 1.0),
        "lightgbm": ("lightgbm_cpu_m5", "lightgbm", 2.0),
        "xgboost": ("xgboost_cpu_v1", "xgboost", 3.0),
    }
    for name, (profile, family, score) in profiles.items():
        baseline = _bundle(tmp_path / f"{name}-baseline", profile=profile, family=family, score=score)
        resumed = _bundle(
            tmp_path / f"{name}-resumed",
            profile=profile,
            family=family,
            score=score,
            reuse=3,
            copy_from=baseline,
        )
        runs[name] = (baseline, resumed)
    corrupted = _bundle(
        tmp_path / "ridge-corruption",
        profile="ridge_golden_v1",
        family="ridge",
        score=1.0,
        reuse=5,
        invalidated=1,
        copy_from=runs["ridge"][0],
    )

    output = build_full_walk_forward_acceptance(
        runs, corruption_rebuild=corrupted, output=tmp_path / "full_walk_forward_acceptance.json"
    )
    result = json.loads(output.read_text(encoding="utf-8"))

    assert result["systemAcceptance"] == "PASS"
    assert result["walkForwardAcceptance"] == "PASS"
    assert result["checkpointRecovery"]["corruptedFoldInvalidatedAndRebuilt"] is True
    assert result["determinism"] == {"ridge": "EXACT", "lightgbm": "EXACT", "xgboost": "EXACT"}
