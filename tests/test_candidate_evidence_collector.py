from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from qlib_platform.cli import parser
from qlib_platform.lineage import sha256_json
from qlib_platform.artifacts.prediction_snapshot import (
    PredictionSnapshotSpec,
    prediction_snapshot_path,
    write_prediction_snapshot,
)
from qlib_platform.research.evidence.collector import collect_candidate_evidence
from qlib_platform.research.contracts.candidate_program import write_candidate_contract_lock
from qlib_platform.research.features.candidate_sets import BENCHMARK_FAMILIES, EXPERIMENT_MATRIX, feature_set
from qlib_platform.research.hypotheses.catalog import hypothesis_definition_sha256
from qlib_platform.research.workflow.candidate_program import write_incremental_acceptance
from qlib_platform.data.store import sha256_file


HYPOTHESIS_FEATURE_SETS = {
    hypothesis_id: (
        f"{hypothesis_id}_CANDIDATE",
        f"{hypothesis_id}_BASELINE",
    )
    for hypothesis_id in (
        "H001",
        "H002",
        "H003",
        "H004",
        "H005",
        "H101",
        "H102",
        "H103",
        "H104",
        "H105",
        "H106",
    )
}


def _contract_lock(tmp_path: Path) -> Path:
    phase1 = tmp_path / "phase1.json"
    phase1.write_text(
        json.dumps(
            {
                "schemaVersion": "alpha_phase1_synthesis_v1",
                "studyId": "phase1-test",
                "status": {"phase1Completion": "COMPLETE_WITH_KNOWN_DATA_GAP"},
                "primaryRecommendation": "REGIME_AWARE_RESEARCH",
                "selectionUsesFinalHoldout": False,
                "publishingAuthorized": False,
                "evidence": {},
            }
        ),
        encoding="utf-8",
    )
    lock_path = write_candidate_contract_lock(
        synthesis_manifest=phase1,
        contract_path="configs/research/ashare_candidate_research_v1.yaml",
        output=tmp_path / "phase2-lock.json",
    )
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["contract"]["multiple_testing"]["romano_wolf_resamples"] = 200
    lock["lockSha256"] = sha256_json({key: value for key, value in lock.items() if key != "lockSha256"})
    lock_path.write_text(json.dumps(lock, sort_keys=True), encoding="utf-8")
    return lock_path


def _release(tmp_path: Path) -> tuple[Path, str]:
    required_components = {
        "bars",
        "daily_basic",
        "adjustment_factors",
        "corporate_actions",
        "trade_status",
        "limit_prices",
        "st_status",
        "security_master",
        "trading_calendar",
        "pit_universe",
        "pit_fundamentals",
        "benchmark",
        "qlib_staging",
        "industry_classification_pit",
    }
    components = [
        {
            "role": role,
            "schemaVersion": {
                "pit_fundamentals": "2",
                "industry_classification_pit": "1",
                "qlib_staging": "qlib-staging-v2",
            }.get(role, "1"),
        }
        for role in sorted(required_components)
    ]
    identity = {
        "schemaVersion": "2.0",
        "profile": "ashare_qlib_research_v2",
        "coverage": {"start": "2023-01-01", "end": "2024-12-31"},
        "requiredComponents": sorted(required_components),
        "components": components,
        "policies": {"pit": "first_open_session_after_max_announcement"},
    }
    identity_sha = sha256_json(identity)
    manifest = {
        **identity,
        "dataReleaseId": f"ds_{identity_sha}",
        "identitySha256": identity_sha,
        "publishedAt": "2024-12-31T00:00:00Z",
    }
    manifest["manifestSha256"] = sha256_json(manifest)
    path = tmp_path / "release.json"
    path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    return path, str(manifest["dataReleaseId"])


def _feature_snapshot(tmp_path: Path, release_id: str, dataset_version: str) -> tuple[Path, str]:
    root = tmp_path / "feature-snapshot"
    root.mkdir()
    partition = root / "year=2023.parquet"
    pd.DataFrame({"dummy": [1.0]}).to_parquet(partition)
    identity = {
        "featureRecipeId": "fr_test",
        "coverage": {"startTime": "2023-01-01", "endTime": "2024-12-31"},
        "files": [{"name": partition.name, "sha256": sha256_file(partition), "rows": 1}],
    }
    snapshot_id = "fs_" + sha256_json(identity)
    manifest = {
        "schemaVersion": "feature_snapshot_v1",
        "featureRecipeId": identity["featureRecipeId"],
        "featureSnapshotId": snapshot_id,
        "contract": {"datasetId": release_id, "datasetVersionId": dataset_version},
        "coverage": identity["coverage"],
        "files": identity["files"],
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root, snapshot_id


def _snapshot(
    tmp_path: Path,
    name: str,
    scores: pd.Series,
    labels: pd.Series,
    *,
    release_id: str,
    feature_snapshot_id: str,
    feature_set_id: str,
    model: str,
) -> tuple[Path, dict[str, object]]:
    path = tmp_path / f"{name}.parquet"
    manifest = write_prediction_snapshot(
        path,
        scores.to_frame("score"),
        labels=labels.to_frame("label"),
        spec=PredictionSnapshotSpec(
            data_release_id=release_id,
            alpha_pack_id=feature_set(feature_set_id).source_pack,
            feature_snapshot_id=feature_snapshot_id,
            label_spec_id="return_5d_t1_v1",
            split_spec_id="wf_test",
            model_id=f"model_{name}",
            model_profile_id=f"{model}_profile_v1",
            fold_id="rolling_oos_aggregate",
            feature_set_id=feature_set_id,
        ),
    )
    return prediction_snapshot_path(path), manifest


def _folds(dates: pd.DatetimeIndex) -> list[dict[str, object]]:
    rows = []
    for number, indices in enumerate(np.array_split(np.arange(len(dates)), 4), start=1):
        rows.append(
            {
                "key": f"rolling_{number:02d}",
                "train": ["2020-01-01", "2022-01-01"],
                "valid": ["2022-02-01", "2022-08-01"],
                "test": [str(dates[indices[0]].date()), str(dates[indices[-1]].date())],
            }
        )
    return rows


def _run_manifest(
    tmp_path: Path,
    name: str,
    snapshot_path: Path,
    snapshot: dict[str, object],
    dates: pd.DatetimeIndex,
    *,
    release_id: str,
    dataset_version: str,
    feature_snapshot_id: str,
    feature_set_id: str,
    model: str,
    hypothesis_id: str | None = None,
    hypothesis_role: str | None = None,
    hypothesis_definition_sha: str | None = None,
) -> Path:
    experiment = {
        "data_release_id": release_id,
        "alpha_pack_id": feature_set(feature_set_id).source_pack,
        "feature_set_id": feature_set_id,
        "feature_set_sha256": feature_set(feature_set_id).fingerprint,
        "label_spec_id": "return_5d_t1_v1",
        "split_profile_id": "wf_1500_126_63_v1",
        "model_profile_id": f"{model}_profile_v1",
        "experiment_id": f"exp_{name}",
    }
    phase2_hypothesis = None
    if hypothesis_id is not None:
        experiment.update(
            {
                "hypothesis_id": hypothesis_id,
                "hypothesis_role": hypothesis_role,
                "hypothesis_definition_sha256": hypothesis_definition_sha,
                "hypothesis_binding_sha256": f"binding_{name}",
            }
        )
        phase2_hypothesis = {
            "hypothesisId": hypothesis_id,
            "role": hypothesis_role,
            "hypothesisDefinitionSha256": hypothesis_definition_sha,
            "hypothesisBindingSha256": f"binding_{name}",
        }
    manifest = {
        "schemaVersion": "2.0",
        "runKind": "phase2_walk_forward",
        "externalRunId": f"run_{name}",
        "dataset": {"datasetId": release_id, "versionId": dataset_version},
        "featureStore": {
            "featureSnapshotId": feature_snapshot_id,
            "datasetVersionId": dataset_version,
        },
        "researchExperimentId": experiment["experiment_id"],
        "researchExperiment": experiment,
        "phase2Hypothesis": phase2_hypothesis,
        "predictionSnapshot": snapshot,
        "runtime": {"modelFamily": model, "modelProfile": f"{model}_profile_v1"},
        "promotion": {"promotionAuthorized": False},
        "folds": _folds(dates),
        "artifacts": [{"name": "oos_predictions.snapshot.json", "localPath": str(snapshot_path)}],
    }
    path = tmp_path / f"run-{name}.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _portfolio(
    tmp_path: Path,
    name: str,
    snapshot: dict[str, object],
    dates: pd.DatetimeIndex,
    *,
    dataset_version: str,
    turnover: float,
) -> Path:
    root = tmp_path / f"portfolio-{name}"
    root.mkdir()
    report_path = root / "portfolio_report.parquet"
    pd.DataFrame(
        {
            "return": 0.0015,
            "bench": 0.0001,
            "cost": 0.0001,
            "turnover": turnover,
        },
        index=dates,
    ).to_parquet(report_path)
    manifest = {
        "schemaVersion": "2.0",
        "runKind": "predictions_only_backtest",
        "dataset": {"versionId": dataset_version},
        "sourcePrediction": {
            "snapshotId": snapshot["snapshotId"],
            "snapshotContract": snapshot["contract"],
        },
        "promotion": {"promotionAuthorized": False},
        "artifacts": [{"name": "portfolio_report.parquet", "localPath": str(report_path)}],
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _evidence_fixture(tmp_path: Path) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    lock_path = _contract_lock(tmp_path)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    release_path, release_id = _release(tmp_path)
    dataset_version = "dv_phase2_test"
    feature_root, feature_snapshot_id = _feature_snapshot(tmp_path, release_id, dataset_version)
    dates = pd.bdate_range("2023-01-02", periods=300)
    instruments = [f"SH{600000 + number:06d}" for number in range(16)]
    index = pd.MultiIndex.from_product([dates, instruments], names=["datetime", "instrument"])
    rng = np.random.default_rng(17)
    label = pd.Series(rng.normal(size=len(index)), index=index, name="label")
    labels_path = tmp_path / "labels.parquet"
    label.to_frame().to_parquet(labels_path)
    benchmark_path = tmp_path / "benchmark.parquet"
    benchmark_columns = sorted({name for values in BENCHMARK_FAMILIES.values() for name in values})
    pd.DataFrame(
        rng.normal(size=(len(index), len(benchmark_columns))),
        index=index,
        columns=benchmark_columns,
    ).to_parquet(benchmark_path)

    ablations: dict[str, object] = {}
    for position, (experiment_id, (feature_set_id, model)) in enumerate(EXPERIMENT_MATRIX.items(), start=1):
        scores = label + pd.Series(
            np.random.default_rng(100 + position).normal(scale=1.5, size=len(index)), index=index
        )
        snapshot_path, snapshot = _snapshot(
            tmp_path,
            f"ablation-{experiment_id}",
            scores,
            label,
            release_id=release_id,
            feature_snapshot_id=feature_snapshot_id,
            feature_set_id=feature_set_id,
            model=model,
        )
        run_path = _run_manifest(
            tmp_path,
            f"ablation-{experiment_id}",
            snapshot_path,
            snapshot,
            dates,
            release_id=release_id,
            dataset_version=dataset_version,
            feature_snapshot_id=feature_snapshot_id,
            feature_set_id=feature_set_id,
            model=model,
        )
        ablations[experiment_id] = {"runManifests": [str(run_path)]}

    candidates: list[dict[str, object]] = []
    hypotheses = {item["hypothesis_id"]: item for item in lock["contract"]["hypotheses"]}
    for position, (hypothesis_id, feature_sets) in enumerate(HYPOTHESIS_FEATURE_SETS.items(), start=1):
        feature_set_id, baseline_feature_set_id = feature_sets
        definition_sha = hypothesis_definition_sha256(hypotheses[hypothesis_id])
        scores = label + pd.Series(
            np.random.default_rng(500 + position).normal(scale=1.5, size=len(index)), index=index
        )
        snapshot_path, snapshot = _snapshot(
            tmp_path,
            hypothesis_id,
            scores,
            label,
            release_id=release_id,
            feature_snapshot_id=feature_snapshot_id,
            feature_set_id=feature_set_id,
            model="ridge",
        )
        run_path = _run_manifest(
            tmp_path,
            hypothesis_id,
            snapshot_path,
            snapshot,
            dates,
            release_id=release_id,
            dataset_version=dataset_version,
            feature_snapshot_id=feature_snapshot_id,
            feature_set_id=feature_set_id,
            model="ridge",
            hypothesis_id=hypothesis_id,
            hypothesis_role="candidate",
            hypothesis_definition_sha=definition_sha,
        )
        portfolio_path = _portfolio(
            tmp_path,
            hypothesis_id,
            snapshot,
            dates,
            dataset_version=dataset_version,
            turnover=0.10,
        )
        baseline_scores = label + pd.Series(
            np.random.default_rng(700 + position).normal(scale=1.8, size=len(index)), index=index
        )
        baseline_snapshot_path, baseline_snapshot = _snapshot(
            tmp_path,
            f"{hypothesis_id}-baseline",
            baseline_scores,
            label,
            release_id=release_id,
            feature_snapshot_id=feature_snapshot_id,
            feature_set_id=baseline_feature_set_id,
            model="ridge",
        )
        baseline_run = _run_manifest(
            tmp_path,
            f"{hypothesis_id}-baseline",
            baseline_snapshot_path,
            baseline_snapshot,
            dates,
            release_id=release_id,
            dataset_version=dataset_version,
            feature_snapshot_id=feature_snapshot_id,
            feature_set_id=baseline_feature_set_id,
            model="ridge",
            hypothesis_id=hypothesis_id,
            hypothesis_role="baseline",
            hypothesis_definition_sha=definition_sha,
        )
        baseline_portfolio = _portfolio(
            tmp_path,
            f"{hypothesis_id}-baseline",
            baseline_snapshot,
            dates,
            dataset_version=dataset_version,
            turnover=0.05,
        )
        candidates.append(
            {
                "candidateId": hypothesis_id,
                "hypothesisId": hypothesis_id,
                "alphaPack": feature_set(feature_set_id).source_pack,
                "featureSet": feature_set_id,
                "model": "ridge",
                "portfolio": "topk_dropout_v1",
                "regimeRule": "none",
                "runManifests": [str(run_path)],
                "predictionSnapshot": str(snapshot_path),
                "baselinePredictionSnapshot": str(baseline_snapshot_path),
                "baselineFeatureSet": baseline_feature_set_id,
                "baselineModel": "ridge",
                "baselineRunManifests": [str(baseline_run)],
                "portfolioManifest": str(portfolio_path),
                "baselinePortfolioManifest": str(baseline_portfolio),
            }
        )
    evidence = {
        "schemaVersion": "phase2_evidence_index_v1",
        "contractLockSha256": lock["lockSha256"],
        "dataReleaseManifest": str(release_path),
        "datasetVersionId": dataset_version,
        "featureSnapshot": str(feature_root),
        "labels": str(labels_path),
        "benchmarkFactorPanel": str(benchmark_path),
        "ablationExperiments": ablations,
        "candidates": candidates,
        "finalHoldout": False,
    }
    evidence_path = tmp_path / "evidence-index.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    return lock_path, evidence_path


def test_collector_builds_one_complete_family_and_candidate_accept_consumes_it(tmp_path: Path):
    lock_path, evidence_path = _evidence_fixture(tmp_path)
    output = collect_candidate_evidence(
        contract_lock=lock_path,
        evidence_index=evidence_path,
        output=tmp_path / "candidate-metrics.json",
    )
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["multipleTesting"]["family"] == list(HYPOTHESIS_FEATURE_SETS)
    assert payload["multipleTesting"]["familySize"] == 11
    assert payload["multipleTesting"]["computedOnce"] is True
    assert payload["multipleTesting"]["testTarget"] == "candidate_minus_baseline_daily_rank_ic"
    assert payload["selectionUsesFinalHoldout"] is False
    assert payload["publishingAuthorized"] is False
    assert len(payload["lineage"]["ablationExperiments"]) == 10
    assert all(candidate["metrics"]["coverage"] == pytest.approx(1.0) for candidate in payload["candidates"])

    acceptance = write_incremental_acceptance(
        contract_lock=lock_path,
        candidate_metrics=output,
        output=tmp_path / "acceptance.json",
    )
    accepted = json.loads(acceptance.read_text(encoding="utf-8"))
    assert len(accepted["candidates"]) == 11
    assert accepted["candidateMetrics"]["collectorSha256"] == payload["collectorSha256"]


def test_collector_rejects_incomplete_family_and_final_holdout(tmp_path: Path):
    lock_path, evidence_path = _evidence_fixture(tmp_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    removed = evidence["candidates"].pop()
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(ValueError, match="complete registered hypothesis family once"):
        collect_candidate_evidence(
            contract_lock=lock_path,
            evidence_index=evidence_path,
            output=tmp_path / "incomplete.json",
        )

    evidence["candidates"].append(removed)
    h101 = next(item for item in evidence["candidates"] if item["hypothesisId"] == "H101")
    h102 = next(item for item in evidence["candidates"] if item["hypothesisId"] == "H102")
    h101["predictionSnapshot"] = h102["predictionSnapshot"]
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(ValueError, match="PredictionSnapshot contract drift"):
        collect_candidate_evidence(
            contract_lock=lock_path,
            evidence_index=evidence_path,
            output=tmp_path / "swapped.json",
        )

    lock_path, evidence_path = _evidence_fixture(tmp_path / "second")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    run_path = Path(evidence["candidates"][0]["runManifests"][0])
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["folds"][0]["final_holdout"] = True
    run_path.write_text(json.dumps(run), encoding="utf-8")
    with pytest.raises(ValueError, match="finalHoldout=false"):
        collect_candidate_evidence(
            contract_lock=lock_path,
            evidence_index=evidence_path,
            output=tmp_path / "holdout.json",
        )


def test_candidate_collect_cli_requires_contract_evidence_and_output():
    args = parser().parse_args(
        [
            "candidate-collect",
            "--contract-lock",
            "lock.json",
            "--evidence",
            "evidence.json",
            "--output",
            "candidate-metrics.json",
        ]
    )
    assert args.command == "candidate-collect"

    acceptance = parser().parse_args(
        [
            "candidate-accept",
            "--contract-lock",
            "lock.json",
            "--candidate-metrics",
            "candidate-metrics.json",
            "--output",
            "acceptance.json",
        ]
    )
    assert acceptance.candidate_metrics == "candidate-metrics.json"
