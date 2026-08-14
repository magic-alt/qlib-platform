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
AUDIT_STATE_METRICS = {"lineage_complete", "dirty_research_override"}


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


def _load_artifact(run_dir: Path, name: str, sort_keys: list[str] | None) -> pd.DataFrame:
    frame = pd.read_parquet(run_dir / name)
    if sort_keys:
        frame = frame.sort_values(sort_keys).reset_index(drop=True)
    return frame


def compare(
    run_dirs: list[Path],
    feature_cold_seconds: float,
    feature_warm_seconds: float,
    promotion_run_dir: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if len(run_dirs) != 3:
        raise ValueError("exactly three independent Ridge run directories are required")
    manifests = [json.loads((path / "manifest.json").read_text(encoding="utf-8")) for path in run_dirs]
    reference = manifests[0]
    contracts = {
        "dataReleaseId": reference["researchExperiment"]["data_release_id"],
        "alphaPackId": reference["researchExperiment"]["alpha_pack_id"],
        "alphaPackSha256": reference["researchExperiment"]["alpha_pack_sha256"],
        "featureSnapshotId": reference["featureStore"]["featureSnapshotId"],
        "labelSpecId": reference["researchExperiment"]["label_spec_id"],
        "splitSpecId": reference["researchExperiment"]["split_sha256"],
        "experimentId": reference["researchExperimentId"],
        "modelProfileId": reference["researchExperiment"]["model_profile_id"],
        "portfolioPolicyId": reference["researchExperiment"]["portfolio_policy_id"],
    }
    contract_checks = {
        key: all(
            value
            == {
                "dataReleaseId": manifest["researchExperiment"]["data_release_id"],
                "alphaPackId": manifest["researchExperiment"]["alpha_pack_id"],
                "alphaPackSha256": manifest["researchExperiment"]["alpha_pack_sha256"],
                "featureSnapshotId": manifest["featureStore"]["featureSnapshotId"],
                "labelSpecId": manifest["researchExperiment"]["label_spec_id"],
                "splitSpecId": manifest["researchExperiment"]["split_sha256"],
                "experimentId": manifest["researchExperimentId"],
                "modelProfileId": manifest["researchExperiment"]["model_profile_id"],
                "portfolioPolicyId": manifest["researchExperiment"]["portfolio_policy_id"],
            }[key]
            for manifest in manifests
        )
        for key, value in contracts.items()
    }
    artifact_checks: dict[str, dict[str, Any]] = {}
    for name, sort_keys in ARTIFACT_SORT_KEYS.items():
        frames = [_load_artifact(path, name, sort_keys) for path in run_dirs]
        hashes = [_content_hash(frame) for frame in frames]
        artifact_checks[name] = {
            "exactlyEqual": frames[0].equals(frames[1]) and frames[0].equals(frames[2]),
            "contentSha256": hashes,
            "rows": len(frames[0]),
        }
    cache_checks = [
        manifest["featureStore"].get("cacheStatus") == "REUSED"
        and manifest["featureStore"].get("rawMaterializationCalls") == 0
        for manifest in manifests
    ]
    metrics_equal = manifests[0]["metrics"] == manifests[1]["metrics"] == manifests[2]["metrics"]
    system_passed = (
        all(contract_checks.values())
        and all(item["exactlyEqual"] for item in artifact_checks.values())
        and all(cache_checks)
        and metrics_equal
    )
    gates = [json.loads((path / "research_gate.json").read_text(encoding="utf-8")) for path in run_dirs]
    failed_research_checks = [item["name"] for item in gates[0]["checks"] if not item["passed"]]
    immutable_golden: dict[str, Any] | None = None
    promotion_passed = False
    if promotion_run_dir is not None:
        promotion_manifest = json.loads((promotion_run_dir / "manifest.json").read_text(encoding="utf-8"))
        promotion_contracts = {
            "dataReleaseId": promotion_manifest["researchExperiment"]["data_release_id"],
            "alphaPackId": promotion_manifest["researchExperiment"]["alpha_pack_id"],
            "alphaPackSha256": promotion_manifest["researchExperiment"]["alpha_pack_sha256"],
            "featureSnapshotId": promotion_manifest["featureStore"]["featureSnapshotId"],
            "labelSpecId": promotion_manifest["researchExperiment"]["label_spec_id"],
            "splitSpecId": promotion_manifest["researchExperiment"]["split_sha256"],
            "experimentId": promotion_manifest["researchExperimentId"],
            "modelProfileId": promotion_manifest["researchExperiment"]["model_profile_id"],
            "portfolioPolicyId": promotion_manifest["researchExperiment"]["portfolio_policy_id"],
        }
        promotion_artifacts: dict[str, dict[str, Any]] = {}
        for name, sort_keys in ARTIFACT_SORT_KEYS.items():
            reference_frame = _load_artifact(run_dirs[0], name, sort_keys)
            promotion_frame = _load_artifact(promotion_run_dir, name, sort_keys)
            promotion_artifacts[name] = {
                "exactlyEqual": reference_frame.equals(promotion_frame),
                "goldenContentSha256": _content_hash(reference_frame),
                "promotionContentSha256": _content_hash(promotion_frame),
                "rows": len(promotion_frame),
            }
        reference_metrics = {
            key: value for key, value in reference["metrics"].items() if key not in AUDIT_STATE_METRICS
        }
        promotion_metrics = {
            key: value
            for key, value in promotion_manifest["metrics"].items()
            if key not in AUDIT_STATE_METRICS
        }
        lineage = promotion_manifest["lineage"]
        promotion_checks = {
            "contractsExactlyEqual": promotion_contracts == contracts,
            "predictionPayloadExactlyEqual": (
                promotion_manifest["predictionSnapshot"]["payload"]["sha256"]
                == reference["predictionSnapshot"]["payload"]["sha256"]
            ),
            "researchMetricsExactlyEqual": promotion_metrics == reference_metrics,
            "artifactsExactlyEqual": all(item["exactlyEqual"] for item in promotion_artifacts.values()),
            "featureSnapshotReused": (
                promotion_manifest["featureStore"].get("cacheStatus") == "REUSED"
                and promotion_manifest["featureStore"].get("rawMaterializationCalls") == 0
            ),
            "committedLineageClean": (
                not bool(lineage.get("qlibPlatformDirty")) and bool(lineage.get("complete"))
            ),
        }
        promotion_passed = system_passed and all(promotion_checks.values())
        immutable_golden = {
            "status": "PASS" if promotion_passed else "FAIL",
            "runId": promotion_manifest["externalRunId"],
            "manifestPath": str(promotion_run_dir / "manifest.json"),
            "qlibPlatformCommit": lineage.get("qlibPlatformCommit"),
            "predictionSnapshotId": promotion_manifest["predictionSnapshot"]["snapshotId"],
            "predictionPayloadSha256": promotion_manifest["predictionSnapshot"]["payload"]["sha256"],
            "checks": promotion_checks,
            "artifacts": promotion_artifacts,
            "auditStateTransition": {
                "lineageComplete": promotion_manifest["metrics"].get("lineage_complete"),
                "dirtyResearchOverride": promotion_manifest["metrics"].get("dirty_research_override"),
            },
        }
    acceptance = {
        "schemaVersion": "full_research_acceptance_v1",
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "systemAcceptance": "PASS" if system_passed else "FAIL",
        "researchQuality": gates[0]["decision"],
        "performanceAcceptance": "BASELINE_RECORDED",
        "baselinePromotion": (
            "IMMUTABLE_GOLDEN"
            if promotion_passed
            else (
                "PENDING_CLEAN_COMMIT"
                if any(bool(manifest["lineage"].get("qlibPlatformDirty")) for manifest in manifests)
                else "ELIGIBLE"
            )
        ),
        "contracts": contracts,
        "contractChecks": contract_checks,
        "featureCache": {
            "featureColdSeconds": feature_cold_seconds,
            "featureWarmSeconds": feature_warm_seconds,
            "runReuseChecks": cache_checks,
        },
        "determinism": {
            "metricsExactlyEqual": metrics_equal,
            "artifacts": artifact_checks,
        },
        "research": {
            "decision": gates[0]["decision"],
            "failedChecks": failed_research_checks,
            "metrics": gates[0]["metrics"],
            "thresholds": gates[0]["thresholds"],
        },
        "immutableGolden": immutable_golden,
        "runs": [
            {
                "runId": manifest["externalRunId"],
                "manifestPath": str(path / "manifest.json"),
                "predictionSnapshotId": manifest["predictionSnapshot"]["snapshotId"],
                "predictionPayloadSha256": manifest["predictionSnapshot"]["payload"]["sha256"],
                "featureCacheStatus": manifest["featureStore"]["cacheStatus"],
                "rawMaterializationCalls": manifest["featureStore"]["rawMaterializationCalls"],
                "totalSeconds": manifest["timings"]["totalSeconds"],
                "peakRssMb": manifest["timings"]["peakRssMb"],
            }
            for path, manifest in zip(run_dirs, manifests)
        ],
    }
    performance = {
        "schemaVersion": "performance_baseline_v1",
        "dataReleaseId": contracts["dataReleaseId"],
        "experimentId": contracts["experimentId"],
        "featureColdSeconds": feature_cold_seconds,
        "featureWarmSeconds": feature_warm_seconds,
        "ridgeRuns": [
            {
                "runId": manifest["externalRunId"],
                "trainSeconds": manifest["timings"]["phasesSeconds"]["train_seconds"],
                "predictSeconds": manifest["timings"]["phasesSeconds"]["predict_seconds"],
                "portfolioSeconds": manifest["timings"]["phasesSeconds"]["portfolio_engine_seconds"],
                "totalSeconds": manifest["timings"]["totalSeconds"],
                "peakRssMb": manifest["timings"]["peakRssMb"],
            }
            for manifest in manifests
        ],
    }
    return acceptance, performance


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dirs", type=Path, nargs=3)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--feature-cold-seconds", type=float, required=True)
    parser.add_argument("--feature-warm-seconds", type=float, required=True)
    parser.add_argument("--promotion-run-dir", type=Path)
    args = parser.parse_args()
    acceptance, performance = compare(
        [path.expanduser().resolve() for path in args.run_dirs],
        args.feature_cold_seconds,
        args.feature_warm_seconds,
        args.promotion_run_dir.expanduser().resolve() if args.promotion_run_dir else None,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    acceptance_path = args.output_dir / "research_acceptance.json"
    performance_path = args.output_dir / "performance_baseline.json"
    acceptance_path.write_text(json.dumps(acceptance, ensure_ascii=False, indent=2), encoding="utf-8")
    performance_path.write_text(json.dumps(performance, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"acceptance": str(acceptance_path), "performance": str(performance_path)}))
    if acceptance["systemAcceptance"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
