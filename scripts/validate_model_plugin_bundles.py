from __future__ import annotations

import argparse
import gc
import json
import pickle
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tushare_qlib.alpha.registry import alpha_pack_from_settings
from tushare_qlib.dataset_resolver import pin_dataset
from tushare_qlib.feature_store import prepare_feature_data
from tushare_qlib.model_bundle import create_model_bundle, load_model_bundle
from tushare_qlib.models.registry import get_model_adapter
from tushare_qlib.research_timing import label_spec_from_settings
from tushare_qlib.runtime_safety import resolve_qlib_parallel_runtime
from tushare_qlib.settings import Settings
from tushare_qlib.train_select import build_dataset


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _model_pickle(repo_root: Path, run_id: str) -> Path:
    matches = sorted((repo_root / "mlruns").glob(f"*/{run_id}/artifacts/params.pkl"))
    if len(matches) != 1:
        raise ValueError(f"expected one saved training object for {run_id}, found {len(matches)}")
    return matches[0]


def _run_bundle_check(
    settings: Settings,
    dataset: Any,
    feature_store: dict[str, object],
    run_dir: Path,
    output_dir: Path,
    repo_root: Path,
) -> dict[str, Any]:
    from qlib.data.dataset.handler import DataHandlerLP

    run = _load_json(run_dir / "manifest.json")
    run_id = str(run["externalRunId"])
    family = str(run["runtime"]["modelFamily"])
    adapter = get_model_adapter(family)
    features = dataset.prepare("test", col_set="feature", data_key=DataHandlerLP.DK_I).sort_index()
    recorded = pd.read_parquet(run_dir / "oos_predictions.parquet").sort_index()["score"]
    if not features.index.equals(recorded.index):
        raise ValueError(f"bundle reference index differs from recorded predictions for {run_id}")

    model_path = _model_pickle(repo_root, run_id)
    with model_path.open("rb") as handle:
        model = pickle.load(handle)
    training_scores = np.asarray(adapter.scores(model, features), dtype=float).reshape(-1)
    recorded_scores = recorded.to_numpy(dtype=float)
    training_vs_recorded_max_abs_diff = float(np.max(np.abs(training_scores - recorded_scores), initial=0.0))
    training_vs_recorded_exact = np.array_equal(training_scores, recorded_scores)

    folds = run["folds"]
    if len(folds) != 1:
        raise ValueError(f"fixed-split bundle acceptance requires exactly one fold: {run_id}")
    fold = folds[0]
    bundle_settings = replace(
        settings,
        paths=replace(settings.paths, models=output_dir / "model_bundles" / family),
    )
    bundle_path = create_model_bundle(
        bundle_settings,
        model=model,
        dataset=dataset,
        family=family,
        model_parameters=run["model"]["parameters"],
        canonical_config=run["canonicalConfig"],
        research_run_id=run_id,
        refit_as_of=fold["test"][1],
        train_window=tuple(fold["train"]),
        valid_window=tuple(fold["valid"]),
        dataset_id=run["researchExperiment"]["data_release_id"],
        dataset_sha256=run["lineage"]["datasetManifestSha256"],
        feature_store=feature_store,
        lineage=run["lineage"],
        seed=int(run["model"]["parameters"].get("seed", 42)),
        runtime=run["runtime"],
        refit_metadata={
            "policy": "model_plugin_acceptance_training_object",
            "trainingObjectArtifact": str(model_path),
            "noRefit": True,
        },
    )
    expected = training_scores.copy()
    del model
    gc.collect()

    loaded = load_model_bundle(bundle_path.parent, device="cpu", verify_parity=True)
    loaded_scores = loaded.predict(features).to_numpy(dtype=float)
    max_abs_diff = float(np.max(np.abs(expected - loaded_scores), initial=0.0))
    exact = np.array_equal(expected, loaded_scores)
    within_tolerance = np.allclose(
        expected,
        loaded_scores,
        rtol=adapter.parity_tolerance,
        atol=adapter.parity_tolerance,
    )
    manifest = loaded.manifest
    checks = {
        "trainingObjectMatchesRecordedPredictions": training_vs_recorded_exact,
        "loadedBundleMatchesTrainingObjectExact": exact,
        "loadedBundleWithinAdapterTolerance": bool(within_tolerance),
        "modelProfileFingerprintRecorded": (
            manifest["runtime"]["profileFingerprint"] == run["runtime"]["profileFingerprint"]
        ),
        "featureSchemaExactlyEqual": loaded.feature_columns == run["lineage"]["featureColumns"],
        "datasetIdentityExactlyEqual": (
            manifest["datasetId"] == run["researchExperiment"]["data_release_id"]
            and manifest["datasetSha256"] == run["lineage"]["datasetManifestSha256"]
        ),
        "featureSnapshotExactlyEqual": (
            manifest["featureStore"]["featureSnapshotId"] == run["featureStore"]["featureSnapshotId"]
        ),
        "implementationChecksumsRecorded": bool(manifest.get("implementationSha256")),
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "runId": run_id,
        "modelFamily": family,
        "modelProfileId": run["researchExperiment"]["model_profile_id"],
        "bundleManifestPath": str(bundle_path),
        "deploymentId": manifest["deploymentId"],
        "adapterTolerance": adapter.parity_tolerance,
        "trainingVsRecordedMaxAbsDiff": training_vs_recorded_max_abs_diff,
        "loadedVsTrainingMaxAbsDiff": max_abs_diff,
        "checks": checks,
    }


def validate(
    settings: Settings,
    lightgbm_dir: Path,
    xgboost_dir: Path,
    output_dir: Path,
    repo_root: Path,
) -> dict[str, Any]:
    settings, _ = pin_dataset(settings)
    import qlib
    from qlib.constant import REG_CN

    parallel = resolve_qlib_parallel_runtime(settings)
    qlib.init(
        provider_uri=str(settings.qlib_data_uri),
        region=REG_CN,
        expression_cache=None,
        dataset_cache=None,
        **parallel.qlib_init_kwargs(),
    )
    run = _load_json(lightgbm_dir / "manifest.json")
    fold = run["folds"][0]
    prepared, feature_store = prepare_feature_data(settings, fold["train"][0], fold["test"][1])
    if feature_store.get("cacheStatus") != "REUSED" or feature_store.get("rawMaterializationCalls") != 0:
        raise ValueError("bundle acceptance must reuse the immutable FeatureSnapshot")
    dataset = build_dataset(
        train=tuple(fold["train"]),
        valid=tuple(fold["valid"]),
        test=tuple(fold["test"]),
        universe=dict(settings.data.get("universe", {})),
        label_spec=label_spec_from_settings(settings),
        alpha_pack=alpha_pack_from_settings(settings),
        prepared_feature_data=prepared,
    )
    results = {
        "lightgbm": _run_bundle_check(settings, dataset, feature_store, lightgbm_dir, output_dir, repo_root),
        "xgboost": _run_bundle_check(settings, dataset, feature_store, xgboost_dir, output_dir, repo_root),
    }
    return {
        "schemaVersion": "model_plugin_bundle_acceptance_v1",
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if all(item["status"] == "PASS" for item in results.values()) else "FAIL",
        "featureStore": feature_store,
        "models": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--lightgbm-run", type=Path, required=True)
    parser.add_argument("--xgboost-run", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parents[1]
    result = validate(
        Settings.load(args.config, require_tushare=False),
        args.lightgbm_run.expanduser().resolve(),
        args.xgboost_run.expanduser().resolve(),
        output_dir,
        repo_root,
    )
    output = output_dir / "bundle_acceptance.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "status": result["status"]}))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
