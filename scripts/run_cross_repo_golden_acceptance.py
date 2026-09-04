#!/usr/bin/env python3
"""Run the real mini DataRelease -> Qlib -> Artifact v2 -> LEAN acceptance loop.

The command intentionally uses an isolated SQLite control plane and isolated
filesystem roots. It never reads credentials or calls a market-data provider;
all input rows are deterministic and are published through the same immutable
DataRelease service used by platform.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


QLIB_INSTRUMENTS = ("SH600001", "SZ000001")
RESULT_MARKER = "__CROSS_REPO_GOLDEN_RESULT__="


def _sha256_json(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _git_commit(root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _platform_stage(
    platform_repo: Path,
    work_dir: Path,
    stage: str,
    *,
    bundle_path: Path | None = None,
) -> dict[str, Any]:
    backend = platform_repo / "web" / "backend"
    python = backend / ".venv" / "bin" / "python"
    if not python.is_file():
        python = backend / ".venv" / "Scripts" / "python.exe"
    helper = platform_repo / "scripts" / "run_cross_repo_golden_platform_stage.py"
    if not python.is_file():
        raise FileNotFoundError(f"lean-platform interpreter not found: {python}")
    if not helper.is_file():
        raise FileNotFoundError(f"lean-platform golden helper not found: {helper}")
    command = [str(python), str(helper), stage, "--work-dir", str(work_dir)]
    if bundle_path is not None:
        command.extend(["--bundle", str(bundle_path)])
    completed = subprocess.run(
        command,
        cwd=platform_repo,
        check=False,
        capture_output=True,
        text=True,
        timeout=900,
    )
    if completed.returncode:
        raise RuntimeError(f"platform golden stage {stage!r} failed:\n{completed.stdout}\n{completed.stderr}")
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith(RESULT_MARKER):
            result = json.loads(line[len(RESULT_MARKER) :])
            if isinstance(result, dict):
                return result
    raise RuntimeError(f"platform golden stage {stage!r} returned no result marker")


def _qlib_settings(work_dir: Path, data_root: Path, release: dict[str, Any]):
    from qlib_platform.settings import Paths, Settings

    release_id = str(release["dataReleaseId"])
    root = work_dir / "qlib" / release_id
    config_path = work_dir / "qlib-golden-config.yaml"
    config_path.write_text("schema_version: golden-v1\n", encoding="utf-8")
    data = {
        "data_source": {
            "kind": "platform_release",
            "platform_release": {
                "id": release_id,
                "data_root": str(data_root),
                "manifest": str(data_root / "releases" / release_id / "manifest.json"),
                "qlib_staging_role": "qlib_staging",
            },
        },
        "universe": {
            "instruments": "csi300_golden",
            "membership_file": str(root / "pit_universe.parquet"),
        },
        "qlib": {"dataset_version": release_id},
    }
    return Settings(
        config_path=config_path,
        data=data,
        paths=Paths.from_root(root),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=root / "current",
    )


def _run_qlib_research(
    work_dir: Path, settings: Any, release_id: str
) -> tuple[Path, dict[str, Any], str, str, float]:
    from qlib.data.dataset import DatasetH
    from qlib.data.dataset.handler import DataHandlerLP
    from qlib.data.dataset.loader import StaticDataLoader
    from qlib_platform.institutional_artifacts import (
        ResearchBundleContext,
        ResearchPromotionStatus,
        export_research_bundle,
    )
    from qlib_platform.model_runtime import (
        ModelProfile,
        build_model,
        resolved_model_parameters,
        resolve_runtime,
    )
    from qlib_platform.platform_release import materialize_platform_release
    from qlib_platform.prediction_snapshot import PredictionSnapshotSpec, write_prediction_snapshot

    materialized = materialize_platform_release(settings)
    if materialized.data_release_id != release_id:
        raise AssertionError("Qlib materialized a different DataRelease")
    staged = pd.concat(
        (pd.read_parquet(path) for path in sorted(settings.paths.staging_full.glob("*.parquet"))),
        ignore_index=True,
    )
    staged = staged.loc[staged["symbol"].isin(QLIB_INSTRUMENTS)].copy()
    staged["date"] = pd.to_datetime(staged["date"])
    staged = staged.sort_values(["symbol", "date"])
    grouped = staged.groupby("symbol", sort=False)
    features = pd.DataFrame(
        {
            "momentum_1d": grouped["close"].pct_change(),
            "momentum_5d": grouped["close"].pct_change(5),
            "open_gap": staged["open"] / staged["close"] - 1.0,
            "log_volume": np.log1p(staged["volume"]),
            "industry_l1_code": pd.to_numeric(staged["industry_l1_code"], errors="raise"),
        }
    )
    label = grouped["close"].shift(-1) / staged["close"] - 1.0
    index = pd.MultiIndex.from_arrays([staged["date"], staged["symbol"]], names=["datetime", "instrument"])
    features.index = index
    label.index = index
    usable = features.notna().all(axis=1) & label.notna()
    features = features.loc[usable].astype(float).sort_index()
    labels = label.loc[usable].astype(float).rename("LABEL0").sort_index()
    dates = pd.DatetimeIndex(features.index.get_level_values("datetime").unique()).sort_values()
    train_end = dates[int(len(dates) * 0.65)]
    valid_end = dates[int(len(dates) * 0.8)]
    segments = {
        "train": (str(dates[0].date()), str(train_end.date())),
        "valid": (str(dates[dates.get_loc(train_end) + 1].date()), str(valid_end.date())),
        "test": (str(dates[dates.get_loc(valid_end) + 1].date()), str(dates[-1].date())),
    }
    raw = pd.concat({"feature": features, "label": labels.to_frame()}, axis=1)
    handler = DataHandlerLP(
        instruments=None,
        start_time=segments["train"][0],
        end_time=segments["test"][1],
        data_loader=StaticDataLoader(raw),
    )
    dataset = DatasetH(handler=handler, segments=segments)
    profile = ModelProfile(
        "ridge_golden_v1", "ridge", "cpu", 0, {"alpha": 1.0, "fit_intercept": False}, "golden"
    )
    runtime = resolve_runtime(profile)
    parameters = resolved_model_parameters(runtime, feature_count=features.shape[1], seed=42, num_threads=1)
    model = build_model(runtime, feature_count=features.shape[1], seed=42, num_threads=1)
    model.fit(dataset)
    prediction = model.predict(dataset, segment="test").rename("score").sort_index()
    prediction_labels = labels.reindex(prediction.index).rename("label")
    coefficients = np.asarray(model.coef_, dtype=float)
    model_id = "ridge_" + _sha256_json(
        {"parameters": parameters, "coefficients": coefficients.tolist(), "release": release_id}
    )
    feature_snapshot_id = (
        "fs_"
        + hashlib.sha256(pd.util.hash_pandas_object(features, index=True).to_numpy().tobytes()).hexdigest()
    )
    prediction_path = work_dir / "qlib-research" / "predictions.parquet"
    snapshot = write_prediction_snapshot(
        prediction_path,
        prediction,
        labels=prediction_labels,
        spec=PredictionSnapshotSpec(
            data_release_id=release_id,
            alpha_pack_id="golden_price_factors_v1",
            feature_snapshot_id=feature_snapshot_id,
            label_spec_id="return_1d_t1_v1",
            split_spec_id="golden_65_15_20_v1",
            model_id=model_id,
            model_profile_id="ridge_golden_v1",
            fold_id="mini_e2e",
        ),
    )
    latest_date = pd.Timestamp(prediction.index.get_level_values("datetime").max())
    latest = prediction.xs(latest_date, level="datetime").sort_values(ascending=False)
    selected = str(latest.index[0])
    selected_score = float(latest.iloc[0])
    staged_dates = pd.DatetimeIndex(staged["date"].unique()).sort_values()
    future_dates = staged_dates[staged_dates > latest_date]
    if future_dates.empty:
        raise AssertionError("Golden fixture has no trade date after its signal")
    trade_date = pd.Timestamp(future_dates[0]).strftime("%Y-%m-%d")
    signal_date = latest_date.strftime("%Y-%m-%d")
    valid_pairs = pd.concat([prediction, prediction_labels], axis=1).dropna()
    ic = float(valid_pairs["score"].corr(valid_pairs["label"]))
    if not np.isfinite(ic) or len(prediction) < 20:
        raise AssertionError("Qlib mini research acceptance gate failed")
    bundle_dir = work_dir / "qlib-research" / "artifact-v2"
    bundle_path = export_research_bundle(
        bundle_dir,
        context=ResearchBundleContext(
            external_run_id="golden_" + snapshot["snapshotId"],
            run_kind="cross_repo_mini_golden",
            data_release_id=release_id,
            git_commit=_git_commit(Path(__file__).resolve().parents[1]),
            container_digest="local-python@sha256:" + _sha256_json(runtime.to_manifest()),
            as_of_time=f"{signal_date}T23:59:59+08:00",
            signal_date=signal_date,
            trade_date=trade_date,
            universe_release_id="CSI300_GOLDEN",
            name="Cross-repository deterministic Ridge golden",
        ),
        promotion_status=ResearchPromotionStatus.RESEARCH_PROMOTED,
        model={
            "family": "ridge",
            "modelId": model_id,
            "parameters": parameters,
            "coefficients": coefficients.tolist(),
            "predictionSnapshotId": snapshot["snapshotId"],
        },
        strategy_policy={"kind": "top1", "grossExposure": 0.8, "signalLagDays": 1},
        signals=[
            {"instrument": str(instrument), "score": float(score)} for instrument, score in latest.items()
        ],
        targets=[{"instrument": selected, "targetWeight": 0.8, "score": selected_score}],
        validation={
            "metrics": {"predictionRows": len(prediction), "ic": ic},
            "gate": {"passed": True, "name": "deterministic_mini_ridge"},
            "predictionSnapshot": snapshot,
        },
    )
    return bundle_path, snapshot, selected, signal_date, selected_score


def run(platform_repo: Path, work_dir: Path) -> dict[str, Any]:
    platform_repo = platform_repo.expanduser().resolve()
    work_dir = work_dir.expanduser().resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(work_dir / "matplotlib"))
    published = _platform_stage(platform_repo, work_dir, "publish")
    release_id = str(published["dataReleaseId"])
    data_root = Path(str(published["dataRoot"]))
    settings = _qlib_settings(work_dir, data_root, {"dataReleaseId": release_id})
    bundle_path, snapshot, selected, signal_date, selected_score = _run_qlib_research(
        work_dir, settings, release_id
    )
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    target = next(item for item in bundle["artifacts"] if item["artifactType"] == "TARGET_PORTFOLIO")
    target_id = str(target["artifactId"])
    targets_sha = str(target["metadata"]["targetsSha256"])
    if any(str(item["dataReleaseId"]) != release_id for item in bundle["artifacts"]):
        raise AssertionError("Artifact graph is not bound to the published DataRelease")
    platform_result = _platform_stage(platform_repo, work_dir, "validate", bundle_path=bundle_path)
    result = {
        "status": "PASS",
        "dataReleaseId": release_id,
        "qlibDataReleaseId": bundle["artifacts"][0]["dataReleaseId"],
        "predictionSnapshotId": snapshot["snapshotId"],
        "predictionDataReleaseId": snapshot["contract"]["data_release_id"],
        "targetPortfolioArtifactId": target_id,
        "targetsSha256": targets_sha,
        **platform_result,
        "selectedInstrument": selected,
        "selectedScore": selected_score,
        "signalDate": signal_date,
        "bundlePath": str(bundle_path),
        "workDir": str(work_dir),
    }
    bound_ids = {
        result["dataReleaseId"],
        result["qlibDataReleaseId"],
        result["predictionDataReleaseId"],
        result["leanDataReleaseId"],
    }
    if len(bound_ids) != 1:
        raise AssertionError(f"DataRelease binding mismatch: {sorted(bound_ids)}")
    if result["leanTargetPortfolioArtifactId"] != target_id:
        raise AssertionError("LEAN target artifact binding mismatch")
    if result["leanTargetsSha256"] != targets_sha:
        raise AssertionError("LEAN target payload binding mismatch")
    evidence_path = work_dir / "cross-repo-golden-result.json"
    evidence_path.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    default_platform = Path(__file__).resolve().parents[2] / "lean-platform"
    parser.add_argument("--platform-repo", type=Path, default=default_platform)
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args()
    work_dir = args.work_dir or Path(tempfile.mkdtemp(prefix="cross-repo-golden-"))
    print(json.dumps(run(args.platform_repo, work_dir), ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
