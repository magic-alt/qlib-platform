from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ARTIFACT_SORT_KEYS = {
    "oos_predictions.parquet": None,
    "oos_labels.parquet": None,
    "portfolio_report.parquet": None,
    "holdings.parquet": ["trade_date", "instrument"],
    "strategy_audit.parquet": ["signal_date", "trade_date", "instrument"],
}
AUDIT_STATE_METRICS = {"unique_artifact", "lineage_complete", "dirty_research_override"}


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _content_hash(frame: pd.DataFrame) -> str:
    schema = json.dumps(
        {
            "indexNames": list(frame.index.names),
            "columns": [str(column) for column in frame.columns],
            "dtypes": [str(dtype) for dtype in frame.dtypes],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    rows = pd.util.hash_pandas_object(frame, index=True, categorize=False).to_numpy().tobytes()
    return hashlib.sha256(schema + rows).hexdigest()


def _artifact(run_dir: Path, name: str) -> pd.DataFrame:
    frame = pd.read_parquet(run_dir / name)
    sort_keys = ARTIFACT_SORT_KEYS[name]
    if sort_keys:
        frame = frame.sort_values(sort_keys).reset_index(drop=True)
    return frame


def _shared_contract(manifest: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    experiment = manifest["researchExperiment"]
    lineage = manifest["lineage"]
    dataset = manifest["dataset"]
    predictions = _artifact(run_dir, "oos_predictions.parquet")
    labels = _artifact(run_dir, "oos_labels.parquet")
    return {
        "dataReleaseId": experiment["data_release_id"],
        "datasetVersionId": dataset["fingerprint"],
        "alphaPackId": experiment["alpha_pack_id"],
        "alphaPackSha256": experiment["alpha_pack_sha256"],
        "featureSnapshotId": manifest["featureStore"]["featureSnapshotId"],
        "featureRecipeId": manifest["featureStore"]["featureRecipeId"],
        "featureSchemaSha256": lineage["featureSchemaSha256"],
        "featureColumns": lineage["featureColumns"],
        "labelSpecId": experiment["label_spec_id"],
        "labelPayloadSha256": _content_hash(labels),
        "splitSpecId": experiment["split_sha256"],
        "folds": manifest["folds"],
        "portfolioPolicyId": experiment["portfolio_policy_id"],
        "portfolioPolicySha256": experiment["portfolio_policy_sha256"],
        "benchmark": experiment["benchmark"],
        "universeMembershipSha256": lineage["universeMembershipSha256"],
        "handlerRows": dataset["handlerRows"],
        "instrumentCount": dataset["instrumentCount"],
        "featureCount": dataset["featureCount"],
        "predictionRows": len(predictions),
        "predictionIndexSha256": _content_hash(pd.DataFrame(index=predictions.index)),
        "oosCalendar": [
            str(pd.Timestamp(value).date())
            for value in predictions.index.get_level_values("datetime").unique()
        ],
    }


def _model_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    gate = manifest["promotion"]
    return {
        "runId": manifest["externalRunId"],
        "modelProfileId": manifest["researchExperiment"]["model_profile_id"],
        "modelProfileSha256": manifest["researchExperiment"]["model_profile_sha256"],
        "modelFamily": manifest["runtime"]["modelFamily"],
        "experimentId": manifest["researchExperimentId"],
        "predictionSnapshotId": manifest["predictionSnapshot"]["snapshotId"],
        "predictionPayloadSha256": manifest["predictionSnapshot"]["payload"]["sha256"],
        "bestIteration": manifest["model"].get("bestIteration"),
        "featureCacheStatus": manifest["featureStore"].get("cacheStatus"),
        "rawMaterializationCalls": manifest["featureStore"].get("rawMaterializationCalls"),
        "system": "PASS",
        "research": gate["decision"],
        "metrics": manifest["metrics"],
        "timings": manifest["timings"],
    }


def _repeatability(run_dirs: list[Path]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifests = [_load_json(path / "manifest.json") for path in run_dirs]
    artifacts: dict[str, Any] = {}
    for name in ARTIFACT_SORT_KEYS:
        frames = [_artifact(path, name) for path in run_dirs]
        hashes = [_content_hash(frame) for frame in frames]
        artifacts[name] = {
            "exactlyEqual": frames[0].equals(frames[1]),
            "contentSha256": hashes,
            "rows": [len(frame) for frame in frames],
        }
    metrics = [
        {key: value for key, value in manifest["metrics"].items() if key not in AUDIT_STATE_METRICS}
        for manifest in manifests
    ]
    checks = {
        "runIdsDifferent": manifests[0]["externalRunId"] != manifests[1]["externalRunId"],
        "experimentIdExactlyEqual": (
            manifests[0]["researchExperimentId"] == manifests[1]["researchExperimentId"]
        ),
        "predictionSnapshotIdsDifferent": (
            manifests[0]["predictionSnapshot"]["snapshotId"]
            != manifests[1]["predictionSnapshot"]["snapshotId"]
        ),
        "predictionPayloadExactlyEqual": (
            manifests[0]["predictionSnapshot"]["payload"]["sha256"]
            == manifests[1]["predictionSnapshot"]["payload"]["sha256"]
        ),
        "bestIterationExactlyEqual": (
            manifests[0]["model"].get("bestIteration") == manifests[1]["model"].get("bestIteration")
        ),
        "researchMetricsExactlyEqual": metrics[0] == metrics[1],
        "allArtifactsExactlyEqual": all(item["exactlyEqual"] for item in artifacts.values()),
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "artifacts": artifacts,
    }, manifests


def compare(
    ridge_dir: Path,
    lightgbm_dirs: list[Path],
    xgboost_dirs: list[Path],
    *,
    bundle_results: Path | None = None,
    portfolio_dirs: list[Path] | None = None,
) -> dict[str, Any]:
    lgb_repeatability, lgb_manifests = _repeatability(lightgbm_dirs)
    xgb_repeatability, xgb_manifests = _repeatability(xgboost_dirs)
    ridge_manifest = _load_json(ridge_dir / "manifest.json")
    representatives = [ridge_manifest, lgb_manifests[0], xgb_manifests[0]]
    representative_dirs = [ridge_dir, lightgbm_dirs[0], xgboost_dirs[0]]
    contracts = [
        _shared_contract(manifest, run_dir) for manifest, run_dir in zip(representatives, representative_dirs)
    ]
    all_manifests = [ridge_manifest, *lgb_manifests, *xgb_manifests]
    profiles = [manifest["researchExperiment"]["model_profile_id"] for manifest in representatives]
    families = [manifest["runtime"]["modelFamily"] for manifest in representatives]
    experiment_ids = [manifest["researchExperimentId"] for manifest in representatives]
    snapshot_ids = [manifest["predictionSnapshot"]["snapshotId"] for manifest in representatives]
    prediction_shas = [manifest["predictionSnapshot"]["payload"]["sha256"] for manifest in representatives]
    isolation_checks = {
        "sharedContractExactlyEqual": contracts[0] == contracts[1] == contracts[2],
        "featureSnapshotExactlyEqual": len({item["featureSnapshotId"] for item in contracts}) == 1,
        "featureSchemaExactlyEqual": len({item["featureSchemaSha256"] for item in contracts}) == 1,
        "labelPayloadExactlyEqual": len({item["labelPayloadSha256"] for item in contracts}) == 1,
        "oosCalendarExactlyEqual": contracts[0]["oosCalendar"]
        == contracts[1]["oosCalendar"]
        == contracts[2]["oosCalendar"],
        "allFeatureSnapshotsReused": all(
            manifest["featureStore"].get("cacheStatus") == "REUSED" for manifest in all_manifests
        ),
        "rawMaterializationCallsZero": all(
            manifest["featureStore"].get("rawMaterializationCalls") == 0 for manifest in all_manifests
        ),
        "modelProfileIdsDifferent": len(set(profiles)) == 3,
        "modelFamiliesDifferent": len(set(families)) == 3,
        "experimentIdsDifferent": len(set(experiment_ids)) == 3,
        "predictionSnapshotIdsDifferent": len(set(snapshot_ids)) == 3,
        "predictionPayloadsDifferent": len(set(prediction_shas)) == 3,
        "cleanCommittedLineage": all(
            not bool(manifest["lineage"].get("qlibPlatformDirty"))
            and bool(manifest["lineage"].get("complete"))
            for manifest in all_manifests
        ),
    }

    bundle = _load_json(bundle_results) if bundle_results else None
    bundle_passed = bundle is not None and bundle.get("status") == "PASS"
    portfolio: dict[str, Any] | None = None
    portfolio_passed = False
    if portfolio_dirs:
        portfolio_manifests = [_load_json(path / "manifest.json") for path in portfolio_dirs]
        source_shas = [manifest["sourcePrediction"]["sha256"] for manifest in portfolio_manifests]
        source_snapshots = [manifest["sourcePrediction"]["snapshotId"] for manifest in portfolio_manifests]
        fingerprints = [manifest["portfolioFingerprint"] for manifest in portfolio_manifests]
        strategies = [manifest["strategy"] for manifest in portfolio_manifests]
        portfolio_checks = {
            "samePredictionPayload": len(set(source_shas)) == 1,
            "samePredictionSnapshot": len(set(source_snapshots)) == 1,
            "portfolioFingerprintsDifferent": len(set(fingerprints)) == len(fingerprints),
            "strategiesDifferent": len({json.dumps(item, sort_keys=True) for item in strategies})
            == len(strategies),
            "zeroFeatureCompute": all(
                manifest.get("executionIsolation", {}).get("featureComputeCalls") == 0
                and manifest.get("executionIsolation", {}).get("rawMaterializationCalls") == 0
                for manifest in portfolio_manifests
            ),
            "zeroModelTrain": all(
                manifest.get("executionIsolation", {}).get("modelTrainCalls") == 0
                for manifest in portfolio_manifests
            ),
            "zeroModelPredict": all(
                manifest.get("executionIsolation", {}).get("modelPredictCalls") == 0
                for manifest in portfolio_manifests
            ),
        }
        portfolio_passed = all(portfolio_checks.values())
        portfolio = {
            "status": "PASS" if portfolio_passed else "FAIL",
            "checks": portfolio_checks,
            "runs": [
                {
                    "runId": manifest["externalRunId"],
                    "strategy": manifest["strategy"],
                    "portfolioFingerprint": manifest["portfolioFingerprint"],
                    "metrics": manifest["metrics"],
                    "timings": manifest["timings"],
                }
                for manifest in portfolio_manifests
            ],
        }

    system_passed = (
        all(isolation_checks.values())
        and lgb_repeatability["status"] == "PASS"
        and xgb_repeatability["status"] == "PASS"
        and bundle_passed
        and portfolio_passed
    )
    return {
        "schemaVersion": "model_plugin_full_research_acceptance_v1",
        "acceptance": "MODEL_PLUGIN_FULL_RESEARCH",
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "systemAcceptance": "PASS" if system_passed else "FAIL",
        "sharedContract": contracts[0],
        "contractMatrix": {profile: contract for profile, contract in zip(profiles, contracts)},
        "modelIsolation": {
            "status": "PASS" if all(isolation_checks.values()) else "FAIL",
            "checks": isolation_checks,
        },
        "models": {
            "ridge_golden_v1": _model_summary(ridge_manifest),
            "lightgbm_cpu_m5": {
                **_model_summary(lgb_manifests[0]),
                "repeatability": lgb_repeatability,
                "runs": [_model_summary(manifest) for manifest in lgb_manifests],
            },
            "xgboost_cpu_v1": {
                **_model_summary(xgb_manifests[0]),
                "repeatability": xgb_repeatability,
                "runs": [_model_summary(manifest) for manifest in xgb_manifests],
            },
        },
        "bundleParity": bundle,
        "predictionOnlyPortfolio": portfolio,
        "performanceAcceptance": "BASELINE_RECORDED",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ridge", type=Path, required=True)
    parser.add_argument("--lightgbm", type=Path, nargs=2, required=True)
    parser.add_argument("--xgboost", type=Path, nargs=2, required=True)
    parser.add_argument("--bundle-results", type=Path)
    parser.add_argument("--portfolio", type=Path, nargs=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare(
        args.ridge.expanduser().resolve(),
        [path.expanduser().resolve() for path in args.lightgbm],
        [path.expanduser().resolve() for path in args.xgboost],
        bundle_results=args.bundle_results.expanduser().resolve() if args.bundle_results else None,
        portfolio_dirs=([path.expanduser().resolve() for path in args.portfolio] if args.portfolio else None),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "systemAcceptance": result["systemAcceptance"]}))
    if result["systemAcceptance"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
