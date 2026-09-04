from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from qlib_platform.research.features.store import FEATURE_STORE_SCHEMA
from qlib_platform.lineage import sha256_json
from qlib_platform.ops.platform_release import DATA_RELEASE_PROFILES, PROFILE_COMPONENT_SCHEMAS
from qlib_platform.artifacts.prediction_snapshot import load_prediction_snapshot
from qlib_platform.research.evaluation.gates import derive_daily_signal_diagnostics
from qlib_platform.data.store import sha256_file
from qlib_platform.research.contracts.candidate_program import (
    MultipleTestingSpec,
    assert_workstream_allowed,
    load_candidate_lock,
)
from qlib_platform.research.features.candidate_sets import BENCHMARK_FAMILIES, EXPERIMENT_MATRIX, feature_set
from qlib_platform.research.hypotheses.catalog import hypothesis_definition_sha256, hypothesis_feature_set
from qlib_platform.research.evaluation.candidate_statistics import multiple_testing_table, nested_ridge_increment


EVIDENCE_INDEX_SCHEMA = "phase2_evidence_index_v1"
CANDIDATE_METRICS_SCHEMA = "phase2_candidate_metrics_v1"


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _sequence(value: object, name: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must be a sequence")
    return value


def _load_json(path: Path, name: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"{name} is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{name} must be a JSON object")
    return payload


def _resolve(base: Path, value: object, name: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{name} path is required")
    source = Path(raw).expanduser()
    return (source if source.is_absolute() else base / source).resolve()


def _artifact_path(manifest: Mapping[str, Any], name: str) -> Path:
    for raw in manifest.get("artifacts", ()):  # type: ignore[union-attr]
        if isinstance(raw, Mapping) and raw.get("name") == name and raw.get("localPath"):
            path = Path(str(raw["localPath"])).expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError(f"run artifact is missing: {path}")
            return path
    raise FileNotFoundError(f"run manifest artifact is missing: {name}")


def _verify_release_manifest(path: Path, expected_profile: str) -> dict[str, Any]:
    manifest = _load_json(path, "DataRelease manifest")
    recorded_manifest_sha = str(manifest.get("manifestSha256") or "")
    actual_manifest_sha = sha256_json(
        {key: value for key, value in manifest.items() if key != "manifestSha256"}
    )
    if recorded_manifest_sha != actual_manifest_sha:
        raise ValueError("DataRelease manifest checksum mismatch")
    identity = {
        key: value
        for key, value in manifest.items()
        if key not in {"dataReleaseId", "identitySha256", "manifestSha256", "publishedAt"}
    }
    identity_sha = sha256_json(identity)
    if (
        manifest.get("identitySha256") != identity_sha
        or manifest.get("dataReleaseId") != f"ds_{identity_sha}"
    ):
        raise ValueError("DataRelease identity mismatch")
    if manifest.get("profile") != expected_profile:
        raise ValueError("DataRelease does not use the Phase 2 profile")
    declared_required = {str(value) for value in manifest.get("requiredComponents", ())}
    if declared_required != set(DATA_RELEASE_PROFILES[expected_profile]):
        raise ValueError("DataRelease requiredComponents does not match the Phase 2 profile")
    components = {
        str(item.get("role")): item
        for item in _sequence(manifest.get("components"), "DataRelease components")
        if isinstance(item, Mapping)
    }
    for role, expected_schema in PROFILE_COMPONENT_SCHEMAS.get(expected_profile, {}).items():
        component = components.get(role)
        if not isinstance(component, Mapping) or str(component.get("schemaVersion") or "") != expected_schema:
            raise ValueError(f"DataRelease component schema mismatch: {role} requires {expected_schema}")
    return manifest


def _verify_feature_snapshot(
    reference: Path,
    *,
    data_release_id: str,
    dataset_version_id: str,
) -> tuple[Path, dict[str, Any]]:
    manifest_path = reference / "manifest.json" if reference.is_dir() else reference
    manifest = _load_json(manifest_path, "FeatureSnapshot manifest")
    if manifest.get("schemaVersion") != FEATURE_STORE_SCHEMA:
        raise ValueError(f"unsupported FeatureSnapshot schema: {manifest.get('schemaVersion')}")
    contract = _mapping(manifest.get("contract"), "FeatureSnapshot contract")
    if contract.get("datasetId") != data_release_id:
        raise ValueError("FeatureSnapshot DataReleaseId mismatch")
    if str(contract.get("datasetVersionId") or "") != dataset_version_id:
        raise ValueError("FeatureSnapshot DatasetVersion mismatch")
    root = manifest_path.parent
    for raw in _sequence(manifest.get("files"), "FeatureSnapshot files"):
        item = _mapping(raw, "FeatureSnapshot file")
        path = (root / str(item.get("name") or "")).resolve()
        if path.parent != root or not path.is_file() or sha256_file(path) != item.get("sha256"):
            raise ValueError(f"FeatureSnapshot partition checksum mismatch: {path.name}")
    snapshot_id = str(manifest.get("featureSnapshotId") or "")
    expected_snapshot_id = "fs_" + sha256_json(
        {
            "featureRecipeId": manifest.get("featureRecipeId"),
            "coverage": manifest.get("coverage"),
            "files": manifest.get("files"),
        }
    )
    if snapshot_id != expected_snapshot_id:
        raise ValueError("FeatureSnapshot identity mismatch")
    return manifest_path, manifest


def _load_indexed_table(path: Path, name: str) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"{name} is missing: {path}")
    if path.suffix.lower() in {".parquet", ".pq"}:
        frame = pd.read_parquet(path)
    elif path.suffix.lower() == ".csv":
        frame = pd.read_csv(path)
    else:
        raise ValueError(f"{name} must be Parquet or CSV")
    if not isinstance(frame.index, pd.MultiIndex) or frame.index.names != ["datetime", "instrument"]:
        date_name = "datetime" if "datetime" in frame else "date" if "date" in frame else None
        if date_name is None or "instrument" not in frame:
            raise ValueError(f"{name} requires datetime/instrument keys")
        frame = frame.copy()
        frame[date_name] = pd.to_datetime(frame[date_name], errors="raise").dt.normalize()
        frame["instrument"] = frame["instrument"].astype(str)
        frame = frame.set_index([date_name, "instrument"])
        frame.index = frame.index.set_names(["datetime", "instrument"])
    else:
        frame = frame.copy()
        dates = pd.to_datetime(frame.index.get_level_values("datetime"), errors="raise").normalize()
        instruments = frame.index.get_level_values("instrument").astype(str)
        frame.index = pd.MultiIndex.from_arrays([dates, instruments], names=["datetime", "instrument"])
    if frame.index.has_duplicates:
        raise ValueError(f"{name} contains duplicate datetime/instrument keys")
    return frame.sort_index()


def _load_labels(path: Path) -> pd.DataFrame:
    frame = _load_indexed_table(path, "Phase 2 labels")
    if "label" not in frame:
        if "LABEL0" in frame:
            frame = frame.rename(columns={"LABEL0": "label"})
        elif len(frame.columns) == 1:
            frame = frame.rename(columns={frame.columns[0]: "label"})
        else:
            raise ValueError("Phase 2 labels require one label column")
    frame = frame[["label"]]
    frame["label"] = pd.to_numeric(frame["label"], errors="coerce")
    if frame["label"].notna().sum() == 0:
        raise ValueError("Phase 2 labels contain no finite observations")
    return frame


def _load_benchmark_panel(path: Path, labels: pd.DataFrame) -> pd.DataFrame:
    frame = _load_indexed_table(path, "benchmark factor panel")
    required = {feature for members in BENCHMARK_FAMILIES.values() for feature in members}
    if missing := required - set(frame):
        raise ValueError(f"benchmark factor panel is missing fields: {sorted(missing)}")
    if not labels.index.isin(frame.index).all():
        raise ValueError("benchmark factor panel does not cover the canonical label keys")
    return frame


def _contains_final_holdout(manifest: Mapping[str, Any]) -> bool:
    if str(manifest.get("runKind") or "").lower() == "final_holdout":
        return True
    for raw in manifest.get("folds", ()):  # type: ignore[union-attr]
        if not isinstance(raw, Mapping):
            continue
        if bool(raw.get("final_holdout")) or str(raw.get("key") or "").lower() == "final_holdout":
            return True
    for key in ("usesFinalHoldout", "finalHoldoutUsed"):
        if manifest.get(key) is not False and key in manifest:
            return True
    for raw in manifest.get("artifacts", ()):  # type: ignore[union-attr]
        if isinstance(raw, Mapping) and "final_holdout" in str(raw.get("name") or "").lower():
            return True
    return False


def _fold_windows(manifests: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, str, str], ...]:
    windows: set[tuple[str, str, str]] = set()
    for manifest in manifests:
        for raw in _sequence(manifest.get("folds"), "run folds"):
            fold = _mapping(raw, "run fold")
            test = _sequence(fold.get("test"), "fold test window")
            if len(test) != 2:
                raise ValueError("fold test window requires start and end")
            start = str(pd.Timestamp(test[0]).normalize().date())
            end = str(pd.Timestamp(test[1]).normalize().date())
            if start > end:
                raise ValueError("fold test window is reversed")
            key = str(fold.get("key") or "fold")
            windows.add((f"{key}:{start}:{end}", start, end))
    ordered = tuple(sorted(windows, key=lambda item: (item[1], item[2], item[0])))
    if not ordered:
        raise ValueError("Phase 2 evidence contains no rolling-OOS fold calendar")
    for previous, current in zip(ordered, ordered[1:]):
        if pd.Timestamp(current[1]) <= pd.Timestamp(previous[2]):
            raise ValueError("Phase 2 rolling-OOS fold calendars overlap")
    return ordered


def _validate_run_manifests(
    paths: Sequence[Path],
    *,
    data_release_id: str,
    dataset_version_id: str,
    feature_snapshot_id: str,
    label_spec_id: str,
    split_profile_id: str,
    expected_feature_set: str,
    expected_model: str,
    expected_hypothesis_id: str | None = None,
    expected_hypothesis_role: str | None = None,
    expected_hypothesis_definition_sha256: str | None = None,
) -> tuple[list[dict[str, Any]], tuple[tuple[str, str, str], ...], list[dict[str, str]]]:
    spec = feature_set(expected_feature_set)
    manifests: list[dict[str, Any]] = []
    lineage: list[dict[str, str]] = []
    for path in paths:
        manifest = _load_json(path, "Phase 2 run manifest")
        if manifest.get("schemaVersion") != "2.0":
            raise ValueError("unsupported Phase 2 run manifest schema")
        if _contains_final_holdout(manifest):
            raise ValueError("Phase 2 evidence must have finalHoldout=false")
        promotion = _mapping(manifest.get("promotion"), "run promotion")
        if promotion.get("promotionAuthorized") is not False:
            raise ValueError("Phase 2 evidence cannot authorize promotion")
        experiment = _mapping(manifest.get("researchExperiment"), "research experiment")
        experiment_id = str(experiment.get("experiment_id") or "")
        if not experiment_id or manifest.get("researchExperimentId") != experiment_id:
            raise ValueError("run experimentId mismatch")
        expected = {
            "data_release_id": data_release_id,
            "alpha_pack_id": spec.source_pack,
            "feature_set_id": expected_feature_set,
            "feature_set_sha256": spec.fingerprint,
            "label_spec_id": label_spec_id,
            "split_profile_id": split_profile_id,
        }
        drift = [key for key, value in expected.items() if experiment.get(key) != value]
        if drift:
            raise ValueError(f"run research contract drift: {drift}")
        if expected_hypothesis_id is not None:
            hypothesis_expected = {
                "hypothesis_id": expected_hypothesis_id,
                "hypothesis_role": expected_hypothesis_role,
                "hypothesis_definition_sha256": expected_hypothesis_definition_sha256,
            }
            if any(experiment.get(key) != value for key, value in hypothesis_expected.items()):
                raise ValueError("run hypothesis binding drift")
            manifest_hypothesis = _mapping(manifest.get("phase2Hypothesis"), "run Phase 2 hypothesis binding")
            if (
                manifest_hypothesis.get("hypothesisId") != expected_hypothesis_id
                or manifest_hypothesis.get("role") != expected_hypothesis_role
                or manifest_hypothesis.get("hypothesisDefinitionSha256")
                != expected_hypothesis_definition_sha256
            ):
                raise ValueError("run hypothesis manifest drift")
        elif manifest.get("phase2Hypothesis") not in (None, {}):
            raise ValueError("ablation run cannot carry a formal hypothesis binding")
        dataset = _mapping(manifest.get("dataset"), "run dataset")
        if (
            dataset.get("datasetId") != data_release_id
            or str(dataset.get("versionId") or "") != dataset_version_id
        ):
            raise ValueError("run DataReleaseId or DatasetVersion mismatch")
        store = _mapping(manifest.get("featureStore"), "run FeatureSnapshot")
        if (
            store.get("featureSnapshotId") != feature_snapshot_id
            or str(store.get("datasetVersionId") or "") != dataset_version_id
        ):
            raise ValueError("run FeatureSnapshot mismatch")
        runtime = _mapping(manifest.get("runtime"), "run model profile")
        if str(runtime.get("modelFamily") or "").lower() != expected_model:
            raise ValueError("run model profile does not match the registered experiment")
        if experiment.get("model_profile_id") != runtime.get("modelProfile"):
            raise ValueError("run model profile identity drift")
        snapshot_path = _artifact_path(manifest, "oos_predictions.snapshot.json")
        _, snapshot = load_prediction_snapshot(snapshot_path)
        if snapshot != manifest.get("predictionSnapshot"):
            raise ValueError("run PredictionSnapshot does not match its immutable artifact")
        snapshot_contract = _mapping(snapshot.get("contract"), "run PredictionSnapshot contract")
        snapshot_expected = {
            "data_release_id": data_release_id,
            "alpha_pack_id": spec.source_pack,
            "feature_set_id": expected_feature_set,
            "feature_snapshot_id": feature_snapshot_id,
            "label_spec_id": label_spec_id,
            "model_profile_id": runtime.get("modelProfile"),
        }
        if any(snapshot_contract.get(key) != value for key, value in snapshot_expected.items()):
            raise ValueError("run PredictionSnapshot contract drift")
        manifests.append(manifest)
        lineage.append(
            {
                "path": str(path),
                "sha256": sha256_file(path),
                "experimentId": experiment_id,
                "predictionSnapshotId": str(snapshot.get("snapshotId") or ""),
            }
        )
    return manifests, _fold_windows(manifests), lineage


def _paths(base: Path, raw: object, name: str) -> list[Path]:
    values = _sequence(raw, name)
    if not values:
        raise ValueError(f"{name} cannot be empty")
    return [_resolve(base, value, name) for value in values]


def _assign_folds(dates: pd.DatetimeIndex, windows: Sequence[tuple[str, str, str]]) -> pd.Series:
    result = pd.Series(pd.NA, index=dates, dtype="string")
    for fold_id, start, end in windows:
        mask = (dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))
        result.loc[mask] = fold_id
    if result.isna().any():
        missing = sorted({str(value.date()) for value in dates[result.isna()]})[:5]
        raise ValueError(f"PredictionSnapshot dates are outside the frozen fold calendar: {missing}")
    return result


def _validate_snapshot(
    path: Path,
    *,
    data_release_id: str,
    feature_snapshot_id: str,
    label_spec_id: str,
    expected_feature_set: str | None = None,
    expected_alpha_pack: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame, manifest = load_prediction_snapshot(path)
    contract = _mapping(manifest.get("contract"), "PredictionSnapshot contract")
    expected: dict[str, object] = {
        "data_release_id": data_release_id,
        "feature_snapshot_id": feature_snapshot_id,
        "label_spec_id": label_spec_id,
    }
    if expected_feature_set is not None:
        expected["feature_set_id"] = expected_feature_set
    if expected_alpha_pack is not None:
        expected["alpha_pack_id"] = expected_alpha_pack
    drift = [key for key, value in expected.items() if contract.get(key) != value]
    if drift:
        raise ValueError(f"PredictionSnapshot contract drift: {drift}")
    if "final_holdout" in str(contract.get("fold_id") or "").lower():
        raise ValueError("Phase 2 evidence must have finalHoldout=false")
    if "label" not in frame:
        raise ValueError("Phase 2 PredictionSnapshot must embed labels")
    return frame, manifest


def _validate_aggregate_snapshot(
    frame: pd.DataFrame,
    run_manifests: Sequence[Mapping[str, Any]],
    name: str,
) -> None:
    components: list[pd.DataFrame] = []
    for manifest in run_manifests:
        component, _ = load_prediction_snapshot(_artifact_path(manifest, "oos_predictions.snapshot.json"))
        components.append(component)
    combined = pd.concat(components).sort_index()
    if combined.index.has_duplicates:
        raise ValueError(f"{name} run PredictionSnapshots overlap")
    if not frame.index.equals(combined.index):
        raise ValueError(f"{name} does not contain exactly the run PredictionSnapshot keys")
    try:
        pd.testing.assert_frame_equal(frame, combined, check_dtype=False, check_exact=True)
    except AssertionError as exc:
        raise ValueError(f"{name} differs from its run PredictionSnapshots") from exc


def _align_snapshot_labels(frame: pd.DataFrame, labels: pd.DataFrame, name: str) -> pd.DataFrame:
    if not frame.index.isin(labels.index).all():
        raise ValueError(f"{name} contains keys outside the canonical labels")
    canonical = labels["label"].reindex(frame.index)
    embedded = pd.to_numeric(frame["label"], errors="coerce")
    equal = embedded.eq(canonical) | (embedded.isna() & canonical.isna())
    if not bool(equal.all()):
        raise ValueError(f"{name} embedded labels drift from the canonical label artifact")
    return pd.DataFrame({"score": frame["score"], "label": canonical}, index=frame.index)


def _validate_portfolio(
    path: Path,
    *,
    snapshot: Mapping[str, Any],
    dataset_version_id: str,
) -> tuple[pd.DataFrame, dict[str, str]]:
    manifest = _load_json(path, "portfolio manifest")
    if manifest.get("runKind") != "predictions_only_backtest":
        raise ValueError("Phase 2 portfolio evidence must be predictions-only")
    if _contains_final_holdout(manifest):
        raise ValueError("Phase 2 portfolio evidence must have finalHoldout=false")
    promotion = _mapping(manifest.get("promotion"), "portfolio promotion")
    if promotion.get("promotionAuthorized") is not False:
        raise ValueError("Phase 2 portfolio evidence cannot authorize promotion")
    source = _mapping(manifest.get("sourcePrediction"), "portfolio source PredictionSnapshot")
    if source.get("snapshotId") != snapshot.get("snapshotId") or source.get(
        "snapshotContract"
    ) != snapshot.get("contract"):
        raise ValueError("portfolio does not reuse the accepted PredictionSnapshot")
    dataset = _mapping(manifest.get("dataset"), "portfolio dataset")
    if str(dataset.get("versionId") or "") != dataset_version_id:
        raise ValueError("portfolio DatasetVersion mismatch")
    report_path = _artifact_path(manifest, "portfolio_report.parquet")
    report = pd.read_parquet(report_path)
    if not isinstance(report.index, pd.DatetimeIndex):
        if "trade_date" not in report:
            raise ValueError("portfolio report requires a DatetimeIndex or trade_date")
        report = report.set_index("trade_date")
    report.index = pd.DatetimeIndex(pd.to_datetime(report.index, errors="raise")).normalize()
    if report.index.has_duplicates:
        raise ValueError("portfolio report dates must be unique")
    required = {"return", "bench", "cost", "turnover"}
    if missing := required - set(report):
        raise ValueError(f"portfolio report is missing fields: {sorted(missing)}")
    for column in sorted(required):
        report[column] = pd.to_numeric(report[column], errors="coerce")
    if report[list(required)].isna().any().any():
        raise ValueError("portfolio report contains non-numeric Gate evidence")
    return report.sort_index(), {
        "path": str(path),
        "sha256": sha256_file(path),
        "reportPath": str(report_path),
        "reportSha256": sha256_file(report_path),
    }


def _daily_rank_ic(frame: pd.DataFrame, direction: str) -> pd.Series:
    diagnostics = derive_daily_signal_diagnostics(frame[["score"]], frame[["label"]])
    orientation = 1.0 if direction == "positive" else -1.0
    result = pd.to_numeric(diagnostics["rank_ic"], errors="coerce") * orientation
    result.index = pd.DatetimeIndex(result.index).normalize()
    return result.rename("oriented_rank_ic")


def _finite_metrics(metrics: Mapping[str, object], candidate_id: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for key, value in metrics.items():
        numeric = float(str(value))
        if not math.isfinite(numeric):
            raise ValueError(f"candidate {candidate_id} produced non-finite metric: {key}")
        result[str(key)] = numeric
    return result


def _candidate_robustness(
    daily: pd.Series,
    *,
    baseline_daily: pd.Series,
    fold_windows: Sequence[tuple[str, str, str]],
    coverage: float,
    portfolio: pd.DataFrame,
    baseline_portfolio: pd.DataFrame,
    testing: MultipleTestingSpec,
    stressed_cost_multiple: float,
) -> dict[str, float]:
    dates = pd.DatetimeIndex(daily.index).normalize()
    folds = _assign_folds(dates, fold_windows)
    fold_means = daily.groupby(folds.to_numpy()).mean()
    rolling = daily.rolling(252, min_periods=252).mean().dropna()
    if rolling.empty:
        raise ValueError("Phase 2 evidence requires at least 252 daily RankIC observations")
    years = sorted(set(dates.year))
    if len(years) < 2:
        raise ValueError("leave-one-year-out evidence requires at least two calendar years")
    leave_one_year = pd.Series(
        {year: float(daily.loc[dates.year != year].mean()) for year in years}, dtype=float
    )
    full_mean = float(daily.mean())
    minimum_leave_one_year = float(leave_one_year.min())
    retention = minimum_leave_one_year / full_mean if full_mean > 0 else 0.0
    incremental = nested_ridge_increment(baseline_daily, daily, hac_lag=testing.hac_lag)
    aligned_portfolio = portfolio.join(
        baseline_portfolio[["bench", "turnover"]].rename(
            columns={"bench": "baseline_bench", "turnover": "baseline_turnover"}
        ),
        how="inner",
    )
    if aligned_portfolio.empty:
        raise ValueError("candidate and baseline portfolio reports do not overlap")
    if not np.allclose(aligned_portfolio["bench"], aligned_portfolio["baseline_bench"], rtol=0.0, atol=1e-12):
        raise ValueError("candidate and baseline portfolio benchmark paths differ")
    return {
        "coverage": coverage,
        "oriented_rank_ic": full_mean,
        "positive_fold_ratio": float(fold_means.gt(0).mean()),
        "incremental_rank_ic": float(incremental["incremental_rank_ic"]),
        "incremental_hac_t": float(incremental["incremental_hac_t"]),
        "worst_fold_rank_ic": float(fold_means.min()),
        "worst_rolling_rank_ic": float(rolling.min()),
        "leave_one_year_min_mean": minimum_leave_one_year,
        "leave_one_year_retention": retention,
        "turnover_increase": float(
            aligned_portfolio["turnover"].mean() - aligned_portfolio["baseline_turnover"].mean()
        ),
        "stressed_net_spread": float(
            (
                aligned_portfolio["return"]
                - aligned_portfolio["bench"]
                - stressed_cost_multiple * aligned_portfolio["cost"]
            ).mean()
        ),
    }


def collect_candidate_evidence(
    *,
    contract_lock: str | Path,
    evidence_index: str | Path,
    output: str | Path,
) -> Path:
    lock_path = Path(contract_lock).expanduser().resolve()
    lock = load_candidate_lock(lock_path)
    assert_workstream_allowed(lock, "INCREMENTAL_ACCEPTANCE")
    index_path = Path(evidence_index).expanduser().resolve()
    evidence = _load_json(index_path, "Phase 2 evidence index")
    if evidence.get("schemaVersion") != EVIDENCE_INDEX_SCHEMA:
        raise ValueError(f"unsupported Phase 2 evidence index: {evidence.get('schemaVersion')}")
    if evidence.get("contractLockSha256") != lock["lockSha256"]:
        raise ValueError("Phase 2 evidence index contract lock mismatch")
    if evidence.get("finalHoldout") is not False:
        raise ValueError("Phase 2 evidence index must set finalHoldout=false")
    base = index_path.parent
    contract = _mapping(lock.get("contract"), "Phase 2 contract")
    release_path = _resolve(base, evidence.get("dataReleaseManifest"), "DataRelease manifest")
    release = _verify_release_manifest(release_path, str(contract.get("data_release_profile") or ""))
    release_id = str(release.get("dataReleaseId") or "")
    dataset_version_id = str(evidence.get("datasetVersionId") or "").strip()
    if not dataset_version_id:
        raise ValueError("Phase 2 evidence index requires DatasetVersion")
    feature_path, feature_manifest = _verify_feature_snapshot(
        _resolve(base, evidence.get("featureSnapshot"), "FeatureSnapshot"),
        data_release_id=release_id,
        dataset_version_id=dataset_version_id,
    )
    feature_snapshot_id = str(feature_manifest["featureSnapshotId"])
    labels_path = _resolve(base, evidence.get("labels"), "labels")
    labels = _load_labels(labels_path)
    benchmark_path = _resolve(base, evidence.get("benchmarkFactorPanel"), "benchmark factor panel")
    benchmark = _load_benchmark_panel(benchmark_path, labels)

    ablations = _mapping(evidence.get("ablationExperiments"), "ablation experiments")
    if set(ablations) != set(EXPERIMENT_MATRIX):
        raise ValueError("ablation experiments must contain exactly P2-01 through P2-10")
    canonical_folds: tuple[tuple[str, str, str], ...] | None = None
    ablation_lineage: dict[str, object] = {}
    for experiment_id, (feature_set_id, model) in EXPERIMENT_MATRIX.items():
        item = _mapping(ablations[experiment_id], f"ablation {experiment_id}")
        manifests, folds, lineage = _validate_run_manifests(
            _paths(base, item.get("runManifests"), f"ablation {experiment_id} runManifests"),
            data_release_id=release_id,
            dataset_version_id=dataset_version_id,
            feature_snapshot_id=feature_snapshot_id,
            label_spec_id=str(contract.get("label_spec") or ""),
            split_profile_id=str(contract.get("split_profile") or ""),
            expected_feature_set=feature_set_id,
            expected_model=model,
        )
        del manifests
        if canonical_folds is None:
            canonical_folds = folds
        elif folds != canonical_folds:
            raise ValueError(f"ablation {experiment_id} fold calendar drift")
        ablation_lineage[experiment_id] = {
            "featureSet": feature_set_id,
            "model": model,
            "runs": lineage,
        }
    assert canonical_folds is not None

    hypotheses = {
        str(item["hypothesis_id"]): item
        for item in _sequence(contract.get("hypotheses"), "registered hypotheses")
        if isinstance(item, Mapping)
    }
    candidates_raw = _sequence(evidence.get("candidates"), "Phase 2 candidates")
    candidate_hypotheses = [
        str(_mapping(item, "Phase 2 candidate").get("hypothesisId") or "") for item in candidates_raw
    ]
    if len(candidate_hypotheses) != len(set(candidate_hypotheses)) or set(candidate_hypotheses) != set(
        hypotheses
    ):
        raise ValueError("candidate evidence must contain the complete registered hypothesis family once")

    testing = MultipleTestingSpec(**_mapping(contract.get("multiple_testing"), "multiple testing"))
    daily_family: dict[str, pd.Series] = {}
    staged: list[dict[str, Any]] = []
    candidate_ids: set[str] = set()
    candidate_snapshot_ids: set[str] = set()
    for raw in candidates_raw:
        item = _mapping(raw, "Phase 2 candidate")
        candidate_id = str(item.get("candidateId") or "").strip()
        hypothesis_id = str(item.get("hypothesisId") or "").strip()
        if not candidate_id or candidate_id in candidate_ids:
            raise ValueError("candidate IDs must be unique and non-empty")
        if candidate_id != hypothesis_id:
            raise ValueError("candidate IDs must exactly match the frozen hypothesis family")
        candidate_ids.add(candidate_id)
        if str(item.get("regimeRule") or "") != "none":
            raise ValueError("candidate-collect runs before regime overlays")
        hypothesis = hypotheses[hypothesis_id]
        definition_sha256 = hypothesis_definition_sha256(hypothesis)
        candidate_spec = hypothesis_feature_set(hypothesis_id, "candidate")
        baseline_spec = hypothesis_feature_set(hypothesis_id, "baseline")
        feature_set_id = str(item.get("featureSet") or "")
        model = str(item.get("model") or "").lower()
        if feature_set_id != candidate_spec.feature_set_id or model != "ridge":
            raise ValueError(f"candidate {candidate_id} is not the registered nested Ridge test")
        spec = feature_set(feature_set_id)
        expected_design = {
            "alphaPack": spec.source_pack,
            "portfolio": str(contract.get("portfolio_policy") or ""),
        }
        if any(item.get(key) != value for key, value in expected_design.items()):
            raise ValueError(f"candidate {candidate_id} frozen design drift")
        run_manifests, folds, run_lineage = _validate_run_manifests(
            _paths(base, item.get("runManifests"), f"candidate {candidate_id} runManifests"),
            data_release_id=release_id,
            dataset_version_id=dataset_version_id,
            feature_snapshot_id=feature_snapshot_id,
            label_spec_id=str(contract.get("label_spec") or ""),
            split_profile_id=str(contract.get("split_profile") or ""),
            expected_feature_set=feature_set_id,
            expected_model=model,
            expected_hypothesis_id=hypothesis_id,
            expected_hypothesis_role="candidate",
            expected_hypothesis_definition_sha256=definition_sha256,
        )
        if folds != canonical_folds:
            raise ValueError(f"candidate {candidate_id} fold calendar drift")
        snapshot_path = _resolve(base, item.get("predictionSnapshot"), "candidate PredictionSnapshot")
        frame, snapshot = _validate_snapshot(
            snapshot_path,
            data_release_id=release_id,
            feature_snapshot_id=feature_snapshot_id,
            label_spec_id=str(contract.get("label_spec") or ""),
            expected_feature_set=feature_set_id,
            expected_alpha_pack=spec.source_pack,
        )
        _validate_aggregate_snapshot(frame, run_manifests, f"candidate {candidate_id}")
        frame = _align_snapshot_labels(frame, labels, f"candidate {candidate_id}")
        candidate_snapshot_id = str(snapshot.get("snapshotId") or "")
        if not candidate_snapshot_id or candidate_snapshot_id in candidate_snapshot_ids:
            raise ValueError("different hypotheses cannot reuse a candidate PredictionSnapshot")
        candidate_snapshot_ids.add(candidate_snapshot_id)
        if not frame.index.isin(benchmark.index).all():
            raise ValueError(f"candidate {candidate_id} is outside the benchmark factor panel")
        baseline_path = _resolve(base, item.get("baselinePredictionSnapshot"), "baseline PredictionSnapshot")
        baseline_frame, baseline_snapshot = _validate_snapshot(
            baseline_path,
            data_release_id=release_id,
            feature_snapshot_id=feature_snapshot_id,
            label_spec_id=str(contract.get("label_spec") or ""),
        )
        if baseline_snapshot.get("snapshotId") == snapshot.get("snapshotId"):
            raise ValueError(f"candidate {candidate_id} reuses its baseline PredictionSnapshot")
        baseline_contract = _mapping(
            baseline_snapshot.get("contract"), "baseline PredictionSnapshot contract"
        )
        baseline_feature_set = str(item.get("baselineFeatureSet") or "")
        baseline_model = str(item.get("baselineModel") or "").lower()
        if (
            baseline_feature_set != baseline_spec.feature_set_id
            or baseline_model != "ridge"
            or baseline_contract.get("feature_set_id") != baseline_feature_set
        ):
            raise ValueError(f"candidate {candidate_id} baseline feature-set drift")
        baseline_runs, baseline_folds, baseline_run_lineage = _validate_run_manifests(
            _paths(
                base,
                item.get("baselineRunManifests"),
                f"candidate {candidate_id} baselineRunManifests",
            ),
            data_release_id=release_id,
            dataset_version_id=dataset_version_id,
            feature_snapshot_id=feature_snapshot_id,
            label_spec_id=str(contract.get("label_spec") or ""),
            split_profile_id=str(contract.get("split_profile") or ""),
            expected_feature_set=baseline_feature_set,
            expected_model=baseline_model,
            expected_hypothesis_id=hypothesis_id,
            expected_hypothesis_role="baseline",
            expected_hypothesis_definition_sha256=definition_sha256,
        )
        if baseline_folds != canonical_folds:
            raise ValueError(f"candidate {candidate_id} baseline fold calendar drift")
        _validate_aggregate_snapshot(baseline_frame, baseline_runs, f"candidate {candidate_id} baseline")
        baseline_frame = _align_snapshot_labels(baseline_frame, labels, f"candidate {candidate_id} baseline")
        direction = str(hypotheses[hypothesis_id]["direction"])
        daily = _daily_rank_ic(frame, direction)
        baseline_daily = _daily_rank_ic(baseline_frame, direction)
        if not daily.index.equals(baseline_daily.index):
            raise ValueError(f"candidate {candidate_id} candidate/baseline daily RankIC date drift")
        _assign_folds(pd.DatetimeIndex(daily.index), folds)
        label_dates = pd.DatetimeIndex(labels.index.get_level_values("datetime")).normalize()
        eligible_date_mask = np.zeros(len(labels), dtype=bool)
        for _, start, end in folds:
            eligible_date_mask |= (label_dates >= pd.Timestamp(start)) & (label_dates <= pd.Timestamp(end))
        eligible_labels = labels.loc[eligible_date_mask, "label"].notna().sum()
        if eligible_labels <= 0:
            raise ValueError(f"candidate {candidate_id} has no eligible canonical labels")
        coverage = float(frame["label"].notna().sum() / eligible_labels)
        portfolio, portfolio_lineage = _validate_portfolio(
            _resolve(base, item.get("portfolioManifest"), "candidate portfolio"),
            snapshot=snapshot,
            dataset_version_id=dataset_version_id,
        )
        baseline_portfolio, baseline_portfolio_lineage = _validate_portfolio(
            _resolve(base, item.get("baselinePortfolioManifest"), "baseline portfolio"),
            snapshot=baseline_snapshot,
            dataset_version_id=dataset_version_id,
        )
        robustness = _candidate_robustness(
            daily,
            baseline_daily=baseline_daily,
            fold_windows=folds,
            coverage=coverage,
            portfolio=portfolio,
            baseline_portfolio=baseline_portfolio,
            testing=testing,
            stressed_cost_multiple=float(contract["robustness"]["stressed_cost_multiple"]),
        )
        daily_family[hypothesis_id] = daily - baseline_daily
        staged.append(
            {
                "candidateId": candidate_id,
                "hypothesisId": hypothesis_id,
                "alphaPack": item["alphaPack"],
                "featureSet": feature_set_id,
                "model": model,
                "portfolio": item["portfolio"],
                "regimeRule": "none",
                "robustness": robustness,
                "evidence": {
                    "runManifests": run_lineage,
                    "predictionSnapshot": {
                        "path": str(snapshot_path),
                        "sha256": sha256_file(snapshot_path),
                        "snapshotId": snapshot["snapshotId"],
                    },
                    "baselinePredictionSnapshot": {
                        "path": str(baseline_path),
                        "sha256": sha256_file(baseline_path),
                        "snapshotId": baseline_snapshot["snapshotId"],
                    },
                    "baselineRunManifests": baseline_run_lineage,
                    "portfolio": portfolio_lineage,
                    "baselinePortfolio": baseline_portfolio_lineage,
                },
            }
        )

    family = pd.concat(
        [daily_family[hypothesis_id].rename(hypothesis_id) for hypothesis_id in hypotheses], axis=1
    ).sort_index()
    testing_table = multiple_testing_table(family, spec=testing).set_index("hypothesis")
    candidates: list[dict[str, Any]] = []
    for item in staged:
        hypothesis_id = str(item["hypothesisId"])
        statistical = testing_table.loc[hypothesis_id]
        metrics = {
            **item.pop("robustness"),
            "hac_t": statistical["hac_t"],
            "bh_q_value": statistical["bh_q_value"],
            "local_fdr": statistical["local_fdr"],
            "romano_wolf_p_value": statistical["romano_wolf_p_value"],
        }
        item["metrics"] = _finite_metrics(metrics, str(item["candidateId"]))
        candidates.append(item)

    payload: dict[str, Any] = {
        "schemaVersion": CANDIDATE_METRICS_SCHEMA,
        "programId": lock["programId"],
        "contractLock": {
            "path": str(lock_path),
            "sha256": sha256_file(lock_path),
            "lockSha256": lock["lockSha256"],
        },
        "evidenceIndex": {"path": str(index_path), "sha256": sha256_file(index_path)},
        "lineage": {
            "dataRelease": {
                "path": str(release_path),
                "dataReleaseId": release_id,
                "manifestSha256": release["manifestSha256"],
                "profile": release["profile"],
            },
            "datasetVersionId": dataset_version_id,
            "featureSnapshot": {
                "path": str(feature_path),
                "sha256": sha256_file(feature_path),
                "featureSnapshotId": feature_snapshot_id,
            },
            "labels": {"path": str(labels_path), "sha256": sha256_file(labels_path)},
            "benchmarkFactorPanel": {
                "path": str(benchmark_path),
                "sha256": sha256_file(benchmark_path),
            },
            "ablationExperiments": ablation_lineage,
        },
        "foldCalendar": [
            {"foldId": fold_id, "start": start, "end": end} for fold_id, start, end in canonical_folds
        ],
        "multipleTesting": {
            "family": list(hypotheses),
            "familySize": len(hypotheses),
            "dateCount": len(family),
            "computedOnce": True,
            "testTarget": "candidate_minus_baseline_daily_rank_ic",
            "table": testing_table.reset_index().to_dict(orient="records"),
        },
        "candidates": sorted(candidates, key=lambda item: str(item["candidateId"])),
        "selectionUsesFinalHoldout": False,
        "publishingAuthorized": False,
    }
    payload["collectorSha256"] = sha256_json(payload)
    target = Path(output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        if existing != payload:
            raise ValueError("existing Phase 2 candidate metrics artifact differs")
        return target
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return target
