from __future__ import annotations

import json
import os
import pickle
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from ..feature_store import FEATURE_STORE_SCHEMA, load_feature_store
from ..full_walk_forward_acceptance import RunEvidence
from ..lineage import git_revision, sha256_json
from ..processor_state import processor_state_manifest
from ..settings import Settings
from ..store import sha256_file
from .attribution_study import ATTRIBUTION_STUDY_SCHEMA
from .factor_taxonomy import load_factor_taxonomy
from .feature_diagnostics import feature_columns
from .model_explanation import (
    derive_explanation_stability,
    derive_model_explanation_summary,
    derive_ridge_importance,
    derive_tree_importance,
    derive_xgb_interactions,
    deterministic_sample_positions,
    load_model_explanation_spec,
    shap_summary_rows,
)
from .regime_study import REGIME_STUDY_SCHEMA
from .study import STUDY_SCHEMA, _mapping


EXPLANATION_STUDY_SCHEMA = "alpha_model_explanation_study_v1"
EXPLANATION_MANIFEST_NAME = "model_explanation_manifest.json"
MODELS = ("ridge", "lightgbm", "xgboost")


def _json_default(value: object) -> object:
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


@dataclass(frozen=True)
class FoldModelInput:
    model: str
    fold: str
    run_id: str
    train: tuple[str, str]
    test: tuple[str, str]
    component_manifest_path: Path
    component_prediction_path: Path
    recorder_model_path: Path
    recorder_prediction_path: Path
    processor_state_sha256: str


def _load_json(path: Path, name: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"{name} is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{name} must be a JSON object")
    return payload


def _validate_manifest_artifacts(path: Path, manifest: Mapping[str, Any], name: str) -> None:
    root = path.parent.resolve()
    seen: set[str] = set()
    for raw in manifest.get("artifacts", []):
        artifact = _mapping(raw, f"{name} artifact")
        artifact_name = str(artifact.get("name") or "")
        if not artifact_name or artifact_name in seen:
            raise ValueError(f"{name} contains a missing or duplicate artifact name")
        seen.add(artifact_name)
        relative = str(artifact.get("path") or "")
        target = (root / relative).resolve()
        if target.parent != root or not target.is_file() or sha256_file(target) != artifact.get("sha256"):
            raise ValueError(f"{name} artifact checksum mismatch: {target}")


def _validate_upstream_studies(
    base_path: Path,
    regime_path: Path,
    attribution_path: Path,
    acceptance_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], pd.DataFrame]:
    base = _load_json(base_path, "feature study manifest")
    regime = _load_json(regime_path, "regime study manifest")
    attribution = _load_json(attribution_path, "attribution study manifest")
    if base.get("schemaVersion") != STUDY_SCHEMA:
        raise ValueError("unsupported feature study schema")
    if regime.get("schemaVersion") != REGIME_STUDY_SCHEMA:
        raise ValueError("unsupported regime study schema")
    if attribution.get("schemaVersion") != ATTRIBUTION_STUDY_SCHEMA:
        raise ValueError("unsupported attribution study schema")
    if _mapping(base.get("status"), "feature study status").get("featureDiagnostics") != "PASS":
        raise ValueError("feature diagnostics must PASS before model explanation")
    regime_status = _mapping(regime.get("status"), "regime study status").get("regimeDiagnostics")
    if regime_status not in {"PASS", "PARTIAL"}:
        raise ValueError("regime diagnostics must be PASS or PARTIAL")
    if _mapping(attribution.get("status"), "attribution status").get("failureAttribution") != "PASS":
        raise ValueError("failure attribution must PASS before model explanation")
    for name, manifest in (("feature", base), ("regime", regime), ("attribution", attribution)):
        if manifest.get("selectionUsesFinalHoldout") is not False:
            raise ValueError(f"{name} study does not prove final-holdout isolation")
        if manifest.get("publishingAuthorized") is not False:
            raise ValueError(f"{name} study unexpectedly authorizes publishing")
    _validate_manifest_artifacts(base_path, base, "feature study")
    _validate_manifest_artifacts(regime_path, regime, "regime study")
    _validate_manifest_artifacts(attribution_path, attribution, "attribution study")
    acceptance_sha = sha256_file(acceptance_path)
    base_contract = _mapping(base.get("contract"), "feature study contract")
    regime_contract = _mapping(regime.get("contract"), "regime study contract")
    attribution_contract = _mapping(attribution.get("contract"), "attribution study contract")
    if (
        _mapping(base_contract.get("fullWalkForwardAcceptance"), "feature acceptance").get("sha256")
        != acceptance_sha
    ):
        raise ValueError("feature study and explanation use different acceptance evidence")
    if regime_contract.get("fullWalkForwardAcceptanceSha256") != acceptance_sha:
        raise ValueError("regime study and explanation use different acceptance evidence")
    if attribution_contract.get("fullWalkForwardAcceptanceSha256") != acceptance_sha:
        raise ValueError("attribution study and explanation use different acceptance evidence")
    if regime_contract.get("baseStudyManifestSha256") != sha256_file(base_path):
        raise ValueError("regime study does not bind the supplied feature study")
    if attribution_contract.get("regimeStudyManifestSha256") != sha256_file(regime_path):
        raise ValueError("attribution study does not bind the supplied regime study")
    labels_entry = [
        item for item in regime.get("artifacts", []) if item.get("name") == "regime_labels.parquet"
    ]
    if len(labels_entry) != 1:
        raise ValueError("regime study must contain exactly one regime_labels.parquet")
    labels_path = regime_path.parent / str(labels_entry[0]["path"])
    return base, regime, attribution, pd.read_parquet(labels_path)


def _accepted_prediction_sha(acceptance: Mapping[str, Any], model: str) -> str:
    return str(
        _mapping(_mapping(acceptance.get("models"), "acceptance models").get(model), model).get(
            "predictionSha256"
        )
        or ""
    )


def _stable_lock(lock: Mapping[str, Any]) -> dict[str, object]:
    return {
        key: lock.get(key)
        for key in (
            "dataRelease",
            "alphaPack",
            "labelSpec",
            "splitSpec",
            "portfolioPolicy",
            "gateThresholds",
            "codeCommit",
            "codeDirty",
            "finalHoldout",
        )
    }


def _artifact_from_component(manifest: Mapping[str, Any], name: str) -> Path:
    matches = [item for item in manifest.get("artifacts", []) if item.get("name") == name]
    if len(matches) != 1:
        raise ValueError(f"component manifest must contain exactly one {name}")
    path = Path(str(matches[0].get("localPath") or "")).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"component artifact is missing: {path}")
    return path


def _normalize_prediction(value: object, name: str) -> pd.DataFrame:
    frame = (
        value.to_frame("score")
        if isinstance(value, pd.Series)
        else value.copy()
        if isinstance(value, pd.DataFrame)
        else None
    )
    if frame is None:
        raise ValueError(f"{name} must be a pandas Series or DataFrame")
    if not isinstance(frame.index, pd.MultiIndex) or frame.index.names != ["datetime", "instrument"]:
        raise ValueError(f"{name} requires a datetime/instrument MultiIndex")
    if "score" not in frame:
        if len(frame.columns) != 1:
            raise ValueError(f"{name} must contain one score column")
        frame = frame.rename(columns={frame.columns[0]: "score"})
    return frame[["score"]].sort_index()


def _assert_prediction_equal(
    actual: pd.DataFrame, expected: pd.DataFrame, name: str, tolerance: float
) -> None:
    if not actual.index.equals(expected.index):
        raise ValueError(f"{name} prediction keys differ")
    if not np.allclose(
        actual["score"].to_numpy(dtype=float),
        expected["score"].to_numpy(dtype=float),
        rtol=tolerance,
        atol=tolerance,
        equal_nan=True,
    ):
        error = float(
            np.nanmax(np.abs(actual["score"].to_numpy(dtype=float) - expected["score"].to_numpy(dtype=float)))
        )
        raise ValueError(f"{name} predictions differ; max_abs_error={error}")


def _resolve_recorder_artifacts(run_id: str, roots: Sequence[Path]) -> tuple[Path, Path]:
    matches: list[Path] = []
    for root in roots:
        if not root.is_dir():
            raise FileNotFoundError(f"model artifact root is missing: {root}")
        direct = root / run_id / "artifacts" / "params.pkl"
        if direct.is_file():
            matches.append(direct)
        matches.extend(
            path
            for path in root.glob(f"*/{run_id}/artifacts/params.pkl")
            if path.is_file() and path not in matches
        )
    unique = sorted({path.resolve() for path in matches})
    if len(unique) != 1:
        raise ValueError(f"recorder model resolution for {run_id} returned {len(unique)} candidates")
    model_path = unique[0]
    prediction_path = model_path.parent / "pred.pkl"
    if not prediction_path.is_file():
        raise FileNotFoundError(f"recorder prediction is missing: {prediction_path}")
    if model_path.parent.parent.name != run_id:
        raise ValueError("recorder model path does not match the component run id")
    return model_path, prediction_path


def _rolling_fold_plan(lock: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    split = _mapping(lock.get("splitSpec"), "selection lock splitSpec")
    raw_folds = split.get("folds")
    if not isinstance(raw_folds, list):
        raise ValueError("selection lock contains no fold plan")
    result: dict[str, Mapping[str, Any]] = {}
    for raw in raw_folds:
        fold = _mapping(raw, "selection lock fold")
        key = str(fold.get("key") or "")
        if bool(fold.get("final_holdout")) or key == "final_holdout":
            continue
        if not key or key in result:
            raise ValueError("selection lock contains an invalid rolling fold")
        result[key] = fold
    if not result:
        raise ValueError("selection lock contains no rolling folds")
    return result


def _load_fold_inputs(
    run: RunEvidence,
    *,
    model: str,
    acceptance: Mapping[str, Any],
    model_roots: Sequence[Path],
    tolerance: float,
) -> tuple[list[FoldModelInput], pd.DataFrame]:
    family = str(_mapping(run.evidence.get("model"), "walk-forward model").get("family") or "")
    if family != model:
        raise ValueError(f"walk-forward model family differs: {family!r} != {model!r}")
    aggregate_path = run.artifact("oos_predictions.parquet")
    if sha256_file(aggregate_path) != _accepted_prediction_sha(acceptance, model):
        raise ValueError(f"{model} predictions do not match Full Walk-forward Acceptance")
    aggregate = _normalize_prediction(pd.read_parquet(aggregate_path), f"{model} aggregate")
    lock = _mapping(run.evidence.get("researchSelectionLock"), f"{model} selection lock")
    plan = _rolling_fold_plan(lock)
    component_runs = run.manifest.get("componentRuns")
    if not isinstance(component_runs, list):
        raise ValueError(f"{model} walk-forward bundle contains no component runs")
    records: dict[str, FoldModelInput] = {}
    for raw in component_runs:
        component = _mapping(raw, f"{model} component run")
        fold = str(component.get("key") or "")
        if fold not in plan:
            continue
        if component.get("portfolioBacktestExecuted") is not False:
            raise ValueError(f"rolling fold {fold} unexpectedly executed a portfolio backtest")
        manifest_path = Path(str(component.get("manifestPath") or "")).expanduser().resolve()
        manifest = _load_json(manifest_path, f"{model} {fold} component manifest")
        run_id = str(component.get("externalRunId") or "")
        if not run_id or manifest.get("externalRunId") != run_id:
            raise ValueError(f"{model} {fold} recorder identity mismatch")
        component_prediction_path = _artifact_from_component(manifest, "oos_predictions.parquet")
        component_prediction = _normalize_prediction(
            pd.read_parquet(component_prediction_path), f"{model} {fold} component"
        )
        fold_spec = plan[fold]
        test = fold_spec.get("test")
        train = fold_spec.get("train")
        if not isinstance(test, list) or len(test) != 2 or not isinstance(train, list) or len(train) != 2:
            raise ValueError(f"{model} {fold} contains invalid train/test windows")
        dates = pd.DatetimeIndex(component_prediction.index.get_level_values("datetime")).normalize()
        if dates.min() < pd.Timestamp(test[0]) or dates.max() > pd.Timestamp(test[1]):
            raise ValueError(f"{model} {fold} predictions escape the certified test window")
        aggregate_slice = aggregate.loc[component_prediction.index]
        _assert_prediction_equal(
            component_prediction, aggregate_slice, f"{model} {fold} aggregate", tolerance
        )
        model_path, recorder_prediction_path = _resolve_recorder_artifacts(run_id, model_roots)
        recorder_prediction = _normalize_prediction(
            pd.read_pickle(recorder_prediction_path), f"{model} {fold} recorder"
        )
        _assert_prediction_equal(
            recorder_prediction, component_prediction, f"{model} {fold} recorder", tolerance
        )
        processor = _mapping(manifest.get("processorState"), f"{model} {fold} processor state")
        processor_sha = str(processor.get("processorStateSha256") or "")
        if not processor_sha:
            raise ValueError(f"{model} {fold} processor state hash is missing")
        if fold in records:
            raise ValueError(f"duplicate {model} rolling component: {fold}")
        records[fold] = FoldModelInput(
            model=model,
            fold=fold,
            run_id=run_id,
            train=(str(train[0]), str(train[1])),
            test=(str(test[0]), str(test[1])),
            component_manifest_path=manifest_path,
            component_prediction_path=component_prediction_path,
            recorder_model_path=model_path,
            recorder_prediction_path=recorder_prediction_path,
            processor_state_sha256=processor_sha,
        )
    if set(records) != set(plan):
        raise ValueError(
            f"{model} rolling model artifacts are incomplete: {sorted(set(plan) - set(records))}"
        )
    return [records[key] for key in sorted(records)], aggregate


def _universe_filter(config: Mapping[str, Any]) -> Any:
    from ..processors import AshareUniverseFilter

    return AshareUniverseFilter(
        min_listed_days=int(str(config.get("min_listed_days", 120))),
        min_circ_mv_yuan=float(str(config.get("min_circ_mv_yuan", 2_000_000_000))),
        min_money_20d_yuan=float(str(config.get("min_money_20d_yuan", 20_000_000))),
        exclude_st=bool(config.get("exclude_st", True)),
        allow_unknown_st=bool(config.get("allow_unknown_st", True)),
    )


def _replay_processors(
    raw_features: pd.DataFrame,
    record: FoldModelInput,
    component_manifest: Mapping[str, Any],
) -> pd.DataFrame:
    from qlib.data.dataset.processor import CSRankNorm, DropnaLabel, Fillna, RobustZScoreNorm
    from ..processors import ProcessInfSingleThread

    canonical = _mapping(component_manifest.get("canonicalConfig"), "component canonical config")
    dataset = _mapping(canonical.get("dataset"), "component dataset config")
    secondary = _mapping(dataset.get("secondary_filters"), "component universe filters")
    shared = _universe_filter(secondary)
    inference = ProcessInfSingleThread()
    normalizer = RobustZScoreNorm(
        fit_start_time=record.train[0],
        fit_end_time=record.train[1],
        fields_group="feature",
        clip_outlier=True,
    )
    fillna = Fillna(fields_group="feature")
    filtered = shared(raw_features.copy())
    inferred = inference(filtered.copy())
    normalizer.fit(inferred)
    normalized = normalizer(inferred.copy())
    processed = fillna(normalized)
    handler = SimpleNamespace(
        shared_processors=[shared],
        infer_processors=[inference, normalizer, fillna],
        learn_processors=[DropnaLabel(), CSRankNorm(fields_group="label")],
    )
    replayed_state = processor_state_manifest(handler, record.train)
    if replayed_state["processorStateSha256"] != record.processor_state_sha256:
        raise ValueError(f"{record.model} {record.fold} processor replay state differs")
    return feature_columns(processed)


def _load_model(path: Path) -> object:
    with path.open("rb") as handle:
        return pickle.load(handle)


def _model_scores_and_shap(
    model_object: object,
    model: str,
    features: pd.DataFrame,
) -> tuple[np.ndarray, np.ndarray | None, Any]:
    if model == "ridge":
        coefficients = np.asarray(getattr(model_object, "coef_", None), dtype=float).reshape(-1)
        if len(coefficients) != features.shape[1]:
            raise ValueError("Ridge model coefficient width differs from OOS features")
        intercept = float(getattr(model_object, "intercept_", 0.0))
        return features.to_numpy(dtype=float) @ coefficients + intercept, None, coefficients
    booster = getattr(model_object, "model", None)
    if booster is None:
        raise ValueError(f"{model} recorder model is not fitted")
    if model == "xgboost":
        import xgboost as xgb

        matrix = xgb.DMatrix(features.to_numpy(dtype=float))
        scores = np.asarray(booster.predict(matrix), dtype=float).reshape(-1)
        contributions = np.asarray(booster.predict(matrix, pred_contribs=True), dtype=float)
    elif model == "lightgbm":
        values = features.to_numpy(dtype=float)
        scores = np.asarray(booster.predict(values), dtype=float).reshape(-1)
        contributions = np.asarray(booster.predict(values, pred_contrib=True), dtype=float)
    else:
        raise ValueError(f"unsupported explanation model: {model}")
    if contributions.shape != (len(features), features.shape[1] + 1):
        raise ValueError(f"{model} TreeSHAP contribution shape differs from the feature contract")
    return scores, contributions, booster


def _aggregate_shap(
    frame: pd.DataFrame,
    group_columns: list[str],
    scope_type: str,
    *,
    minimum_sessions: int | None = None,
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    rows: list[dict[str, object]] = []
    metrics = (
        "mean_shap",
        "mean_abs_shap",
        "normalized_mean_abs_shap",
        "shap_std",
        "feature_shap_spearman",
        "additivity_max_abs_error",
    )
    for keys, block in frame.groupby(group_columns, sort=True, dropna=False):
        key_values = keys if isinstance(keys, tuple) else (keys,)
        weights = pd.to_numeric(block["observations"], errors="coerce").fillna(0).to_numpy(dtype=float)
        row = {column: value for column, value in zip(group_columns, key_values, strict=True)}
        total_sessions = int(block["sessions"].sum())
        row.update(
            {
                "scope_type": scope_type,
                "scope": "ALL_OOS" if scope_type == "ALL_OOS" else row.get("scope"),
                "fold": None,
                "observations": int(block["observations"].sum()),
                "sessions": total_sessions,
                "sample_status": (
                    "SUFFICIENT"
                    if minimum_sessions is not None and total_sessions >= minimum_sessions
                    else "AVAILABLE"
                    if minimum_sessions is None
                    else "INSUFFICIENT_SAMPLE"
                ),
            }
        )
        for metric in metrics:
            values = pd.to_numeric(block[metric], errors="coerce").to_numpy(dtype=float)
            valid = np.isfinite(values) & (weights > 0)
            row[metric] = (
                float(np.average(values[valid], weights=weights[valid])) if valid.any() else float("nan")
            )
        first = block.iloc[0]
        for column in ("family", "role", "direction"):
            row[column] = first[column]
        rows.append(row)
    result = pd.DataFrame(rows)
    rank_groups = [column for column in group_columns if column != "feature"]
    result["normalized_mean_abs_shap"] = result.groupby(rank_groups, dropna=False)["mean_abs_shap"].transform(
        lambda values: values / values.sum() if float(values.sum()) > 0 else 0.0
    )
    result["rank"] = (
        result.groupby(rank_groups, dropna=False)["normalized_mean_abs_shap"]
        .rank(method="first", ascending=False)
        .astype(int)
    )
    return result


def _aggregate_importance(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for (model, feature, importance_type), block in frame.groupby(
        ["model", "feature", "importance_type"], sort=True
    ):
        first = block.iloc[0]
        rows.append(
            {
                "model": model,
                "scope_type": "ALL_OOS",
                "scope": "ALL_OOS",
                "fold": None,
                "feature": feature,
                "family": first["family"],
                "role": first["role"],
                "direction": first["direction"],
                "importance_type": importance_type,
                "raw_importance": float(pd.to_numeric(block["raw_importance"], errors="coerce").mean()),
                "normalized_importance": float(
                    pd.to_numeric(block["normalized_importance"], errors="coerce").mean()
                ),
                "sample_status": "AVAILABLE",
            }
        )
    result = pd.DataFrame(rows)
    result["rank"] = (
        result.groupby(["model", "importance_type"])["normalized_importance"]
        .rank(method="first", ascending=False)
        .astype(int)
    )
    return pd.concat([frame, result], ignore_index=True)


def _aggregate_interactions(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    fold_count = int(frame["fold"].nunique())
    rows: list[dict[str, object]] = []
    for (feature_1, feature_2), block in frame.groupby(["feature_1", "feature_2"], sort=True):
        first = block.iloc[0]
        rows.append(
            {
                "scope_type": "ALL_OOS",
                "scope": "ALL_OOS",
                "fold": None,
                "feature_1": feature_1,
                "feature_2": feature_2,
                "family_1": first["family_1"],
                "family_2": first["family_2"],
                "observations": int(block["observations"].sum()),
                "sessions": int(block["sessions"].sum()),
                "mean_abs_pair_interaction": float(block["mean_abs_pair_interaction"].mean()),
                "normalized_share": float(block["normalized_share"].mean()),
                "fold_presence_rate": block["fold"].nunique() / fold_count if fold_count else float("nan"),
                "sample_status": "AVAILABLE",
            }
        )
    aggregate = pd.DataFrame(rows).sort_values(
        ["normalized_share", "feature_1", "feature_2"], ascending=[False, True, True], kind="stable"
    )
    aggregate["rank"] = np.arange(1, len(aggregate) + 1)
    fold_frame = frame.copy()
    fold_frame["fold_presence_rate"] = np.nan
    return pd.concat([fold_frame, aggregate], ignore_index=True)


def _artifact_entry(path: Path, *, rows: int | None = None) -> dict[str, object]:
    result: dict[str, object] = {"name": path.name, "path": path.name, "sha256": sha256_file(path)}
    if rows is not None:
        result["rows"] = rows
    return result


def _validate_existing(path: Path, contract: Mapping[str, Any]) -> Path:
    manifest_path = path / EXPLANATION_MANIFEST_NAME
    manifest = _load_json(manifest_path, "existing model explanation manifest")
    if manifest.get("contract") != dict(contract):
        raise ValueError(f"existing model explanation contract differs: {path}")
    _validate_manifest_artifacts(manifest_path, manifest, "model explanation")
    return manifest_path


def _write_report(path: Path, study_id: str, summary: Mapping[str, Any], regime_status: str) -> None:
    lines = [
        "# Alpha Research Phase 1 — Model Explanation",
        "",
        f"- Study ID: `{study_id}`",
        "- Model Explanation: PASS",
        f"- Regime Conditioning: {regime_status}",
        "- Model Artifact Certification: DERIVED_SAME_RECORDER_ADDITIVITY",
        "- Model Train Calls: 0",
        "- New Prediction Artifacts: 0",
        "- Feature Materialization Calls: 0",
        "- Selection Uses Final Holdout: false",
        "- Publishing Authorized: false",
        "",
        "The accepted rolling predictions did not originally hash fold model binaries. This study binds each existing recorder model retrospectively through recorder identity, fold prediction parity, processor-state replay, and TreeSHAP additivity. It does not claim Full Acceptance certified the binaries directly.",
        "",
        "## Hypothesis assessment",
        "",
        "```json",
        json.dumps(
            summary,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            default=_json_default,
        ),
        "```",
        "",
        "Permutation importance and retraining-based bounded sensitivity remain not run. Neither absent test is treated as positive tuning evidence.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _materialize_bundle(
    output_root: Path,
    *,
    contract: dict[str, Any],
    frames: Mapping[str, pd.DataFrame],
    summary: Mapping[str, Any],
    regime_status: str,
) -> Path:
    study_id = "ame_" + sha256_json(contract)
    output_root.mkdir(parents=True, exist_ok=True)
    target = output_root / study_id
    if target.exists():
        return _validate_existing(target, contract)
    building = Path(tempfile.mkdtemp(prefix=f".{study_id}.", dir=output_root))
    try:
        artifacts: list[dict[str, object]] = []
        for name, frame in frames.items():
            artifact_path = building / name
            frame.to_parquet(artifact_path, index=False)
            artifacts.append(_artifact_entry(artifact_path, rows=len(frame)))
        summary_path = building / "model_explanation_summary.json"
        summary_path.write_text(
            json.dumps(
                summary,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                default=_json_default,
            ),
            encoding="utf-8",
        )
        artifacts.append(_artifact_entry(summary_path))
        report_path = building / "model_explanation_report.md"
        _write_report(report_path, study_id, summary, regime_status)
        artifacts.append(_artifact_entry(report_path))
        manifest = {
            "schemaVersion": EXPLANATION_STUDY_SCHEMA,
            "studyId": study_id,
            "studyType": "ALPHA_RESEARCH_PHASE1_MODEL_EXPLANATION",
            "contract": contract,
            "status": {
                "systemIntegrity": "PASS",
                "featureDiagnostics": "PASS",
                "regimeDiagnostics": regime_status,
                "failureAttribution": "PASS",
                "modelExplanation": "PASS",
                "regimeConditioning": regime_status,
                "boundedSensitivity": "NOT_RUN_NO_RETRAIN_AUTHORIZED",
                "permutationImportance": "NOT_RUN_NO_PREDICT_AUTHORIZED",
            },
            "modelArtifactCertification": "DERIVED_SAME_RECORDER_ADDITIVITY",
            "primaryMechanism": summary["xgbPrimaryMechanism"],
            "executionIsolation": {
                "modelTrainCalls": 0,
                "ordinaryModelPredictCalls": 0,
                "newPredictionArtifacts": 0,
                "featureMaterializationCalls": 0,
                "portfolioBacktestCalls": 0,
                "explanationEvaluationCalls": contract["explanationEvaluationCalls"],
            },
            "selectionUsesFinalHoldout": False,
            "publishingAuthorized": False,
            "artifacts": artifacts,
        }
        manifest_path = building / EXPLANATION_MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
        )
        try:
            os.replace(building, target)
        except OSError:
            if target.exists():
                return _validate_existing(target, contract)
            raise
        return target / EXPLANATION_MANIFEST_NAME
    finally:
        if building.exists():
            shutil.rmtree(building, ignore_errors=True)


def run_explanation_diagnose(
    settings: Settings,
    *,
    base_study: str | Path,
    regime_study: str | Path,
    attribution_study: str | Path,
    acceptance: str | Path,
    ridge_walk_forward: str | Path,
    lightgbm_walk_forward: str | Path,
    xgboost_walk_forward: str | Path,
    feature_snapshot: str | Path,
    taxonomy_path: str | Path,
    model_artifact_roots: Sequence[str | Path],
    explanation_path: str | Path,
    output_root: str | Path | None = None,
) -> Path:
    base_path = Path(base_study).expanduser().resolve()
    regime_path = Path(regime_study).expanduser().resolve()
    attribution_path = Path(attribution_study).expanduser().resolve()
    acceptance_path = Path(acceptance).expanduser().resolve()
    feature_root = Path(feature_snapshot).expanduser().resolve()
    model_roots = [Path(value).expanduser().resolve() for value in model_artifact_roots]
    if not model_roots:
        raise ValueError("at least one model artifact root is required")
    base, regime, attribution, regime_labels = _validate_upstream_studies(
        base_path, regime_path, attribution_path, acceptance_path
    )
    acceptance_payload = _load_json(acceptance_path, "Full Walk-forward Acceptance")
    if (
        acceptance_payload.get("acceptanceType") != "FULL_WALK_FORWARD_V1"
        or acceptance_payload.get("systemAcceptance") != "PASS"
        or acceptance_payload.get("walkForwardAcceptance") != "PASS"
    ):
        raise ValueError("Full Walk-forward Acceptance is not certified")
    if (
        _mapping(acceptance_payload.get("finalHoldout"), "acceptance final holdout").get(
            "usedForResearchSelection"
        )
        is not False
    ):
        raise ValueError("acceptance does not prove final-holdout isolation")
    spec = load_model_explanation_spec(explanation_path)
    runs = {
        "ridge": RunEvidence.load(ridge_walk_forward),
        "lightgbm": RunEvidence.load(lightgbm_walk_forward),
        "xgboost": RunEvidence.load(xgboost_walk_forward),
    }
    first_lock = _mapping(runs["ridge"].evidence.get("researchSelectionLock"), "ridge selection lock")
    if any(
        _stable_lock(_mapping(run.evidence.get("researchSelectionLock"), f"{model} selection lock"))
        != _stable_lock(first_lock)
        for model, run in runs.items()
    ):
        raise ValueError("model explanation walk-forward contracts differ")
    fold_inputs: dict[str, list[FoldModelInput]] = {}
    aggregate_predictions: dict[str, pd.DataFrame] = {}
    for model, run in runs.items():
        fold_inputs[model], aggregate_predictions[model] = _load_fold_inputs(
            run,
            model=model,
            acceptance=acceptance_payload,
            model_roots=model_roots,
            tolerance=spec.score_parity_tolerance,
        )
    fold_sets = {model: [record.fold for record in records] for model, records in fold_inputs.items()}
    if len({tuple(values) for values in fold_sets.values()}) != 1:
        raise ValueError("model explanation rolling fold sets differ")
    feature_manifest = _load_json(feature_root / "manifest.json", "FeatureSnapshot manifest")
    if feature_manifest.get("schemaVersion") != FEATURE_STORE_SCHEMA:
        raise ValueError("unsupported FeatureSnapshot schema")
    feature_contract = _mapping(base.get("contract"), "feature study contract")
    if feature_manifest.get("featureSnapshotId") != feature_contract.get("featureSnapshotId"):
        raise ValueError("model explanation and feature study FeatureSnapshot differ")
    if sha256_file(feature_root / "manifest.json") != feature_contract.get("featureSnapshotManifestSha256"):
        raise ValueError("model explanation FeatureSnapshot manifest differs")
    all_records = [record for records in fold_inputs.values() for record in records]
    start = min(record.train[0] for record in all_records)
    end = max(record.test[1] for record in all_records)
    raw_features = load_feature_store(feature_root, start, end, verify_checksums=True)
    names = list(feature_columns(raw_features).columns)
    locked_alpha = _mapping(first_lock.get("alphaPack"), "selection lock AlphaPack")
    taxonomy = load_factor_taxonomy(
        taxonomy_path,
        expected_features=names,
        expected_alpha_pack_id=str(locked_alpha.get("id") or ""),
    )
    regime_contract = _mapping(regime.get("contract"), "regime study contract")
    if regime_contract.get("taxonomySha256") != taxonomy.semantic_sha256:
        raise ValueError("model explanation and regime study taxonomy differ")
    prediction_hashes = {
        model: sha256_file(run.artifact("oos_predictions.parquet")) for model, run in runs.items()
    }
    if (
        _mapping(regime_contract.get("modelPredictionSha256"), "regime prediction hashes")
        != prediction_hashes
    ):
        raise ValueError("model explanation and regime study predictions differ")
    attribution_contract = _mapping(attribution.get("contract"), "attribution contract")
    if (
        _mapping(attribution_contract.get("modelPredictionSha256"), "attribution prediction hashes")
        != prediction_hashes
    ):
        raise ValueError("model explanation and attribution predictions differ")

    importance_frames: list[pd.DataFrame] = []
    shap_fold_rows: list[dict[str, object]] = []
    shap_year_rows: list[dict[str, object]] = []
    shap_regime_rows: list[dict[str, object]] = []
    interaction_frames: list[pd.DataFrame] = []
    model_inputs_contract: dict[str, dict[str, object]] = {}
    evaluation_calls = 0
    processed_cache: dict[tuple[str, str], pd.DataFrame] = {}
    for model in MODELS:
        model_inputs_contract[model] = {}
        for record in fold_inputs[model]:
            component_manifest = _load_json(
                record.component_manifest_path, f"{model} {record.fold} component manifest"
            )
            cache_key = (record.fold, record.processor_state_sha256)
            if cache_key not in processed_cache:
                processed_cache[cache_key] = _replay_processors(raw_features, record, component_manifest)
            processed = processed_cache[cache_key]
            accepted = _normalize_prediction(
                pd.read_parquet(record.component_prediction_path), f"{model} {record.fold} accepted"
            )
            missing = accepted.index.difference(processed.index)
            if len(missing):
                raise ValueError(f"processor replay is missing {len(missing)} {model} {record.fold} OOS rows")
            oos = processed.reindex(accepted.index)
            if list(oos.columns) != names:
                raise ValueError("processor replay changed the feature order")
            model_object = _load_model(record.recorder_model_path)
            scores, contributions, native = _model_scores_and_shap(model_object, model, oos)
            evaluation_calls += 1
            actual = pd.DataFrame({"score": scores}, index=oos.index)
            _assert_prediction_equal(
                actual, accepted, f"{model} {record.fold} model parity", spec.score_parity_tolerance
            )
            additivity_error = 0.0
            if model == "ridge":
                importance_frames.append(
                    derive_ridge_importance(native, fold=record.fold, feature_names=names, taxonomy=taxonomy)
                )
            else:
                assert contributions is not None
                additivity_error = float(np.max(np.abs(contributions.sum(axis=1) - scores)))
                if additivity_error > spec.shap_additivity_tolerance:
                    raise ValueError(f"{model} {record.fold} TreeSHAP additivity failed: {additivity_error}")
                importance_frames.append(
                    derive_tree_importance(
                        native,
                        model=model,
                        fold=record.fold,
                        feature_names=names,
                        taxonomy=taxonomy,
                    )
                )
                shap_values = contributions[:, :-1]
                shap_fold_rows.extend(
                    shap_summary_rows(
                        oos,
                        shap_values,
                        model=model,
                        scope_type="FOLD",
                        scope=record.fold,
                        fold=record.fold,
                        taxonomy=taxonomy,
                        additivity_max_abs_error=additivity_error,
                    )
                )
                dates = pd.DatetimeIndex(oos.index.get_level_values("datetime")).normalize()
                for year in sorted(set(dates.year)):
                    mask = dates.year == year
                    shap_year_rows.extend(
                        shap_summary_rows(
                            oos.loc[mask],
                            shap_values[mask],
                            model=model,
                            scope_type="YEAR",
                            scope=str(year),
                            fold=record.fold,
                            taxonomy=taxonomy,
                            additivity_max_abs_error=additivity_error,
                        )
                    )
                available = regime_labels.loc[regime_labels["status"].eq("AVAILABLE")].copy()
                available["date"] = pd.to_datetime(available["date"]).dt.normalize()
                for (dimension, state), label_block in available.groupby(["dimension", "state"], sort=True):
                    regime_dates = set(pd.DatetimeIndex(label_block["date"]).normalize())
                    mask = np.asarray([date in regime_dates for date in dates], dtype=bool)
                    if not mask.any():
                        continue
                    sessions = int(pd.DatetimeIndex(dates[mask]).nunique())
                    sample_status = (
                        "SUFFICIENT" if sessions >= spec.minimum_regime_sessions else "INSUFFICIENT_SAMPLE"
                    )
                    rows = shap_summary_rows(
                        oos.loc[mask],
                        shap_values[mask],
                        model=model,
                        scope_type="REGIME_FOLD",
                        scope=f"{dimension}:{state}",
                        fold=record.fold,
                        taxonomy=taxonomy,
                        additivity_max_abs_error=additivity_error,
                        sample_status=sample_status,
                        dimension=str(dimension),
                        state=str(state),
                    )
                    shap_regime_rows.extend(rows)
                if model == "xgboost":
                    import xgboost as xgb

                    positions = deterministic_sample_positions(
                        oos.index,
                        count=spec.interaction_rows_per_fold,
                        seed=spec.random_seed,
                        namespace=record.fold,
                    )
                    sample = oos.iloc[positions]
                    raw_interactions = np.asarray(
                        native.predict(xgb.DMatrix(sample.to_numpy(dtype=float)), pred_interactions=True),
                        dtype=float,
                    )
                    evaluation_calls += 1
                    interaction_values = raw_interactions[:, :-1, :-1]
                    interaction_frames.append(
                        derive_xgb_interactions(
                            interaction_values,
                            fold=record.fold,
                            feature_names=names,
                            taxonomy=taxonomy,
                            top_pairs=spec.interaction_top_pairs,
                            observations=len(sample),
                            sessions=int(sample.index.get_level_values("datetime").nunique()),
                        )
                    )
            model_inputs_contract[model][record.fold] = {
                "runId": record.run_id,
                "componentManifestSha256": sha256_file(record.component_manifest_path),
                "componentPredictionSha256": sha256_file(record.component_prediction_path),
                "recorderModelSha256": sha256_file(record.recorder_model_path),
                "recorderPredictionSha256": sha256_file(record.recorder_prediction_path),
                "processorStateSha256": record.processor_state_sha256,
                "scoreParityTolerance": spec.score_parity_tolerance,
                "shapAdditivityMaxAbsError": additivity_error if model != "ridge" else None,
            }
    fold_importance = pd.concat(importance_frames, ignore_index=True)
    feature_importance = _aggregate_importance(fold_importance)
    shap_by_fold = pd.DataFrame(shap_fold_rows)
    shap_summary = _aggregate_shap(
        shap_by_fold,
        ["model", "feature"],
        "ALL_OOS",
    )
    shap_by_year_fold = pd.DataFrame(shap_year_rows)
    shap_by_year = _aggregate_shap(
        shap_by_year_fold,
        ["model", "scope", "feature"],
        "YEAR",
    )
    shap_by_regime_fold = pd.DataFrame(shap_regime_rows)
    shap_by_regime = _aggregate_shap(
        shap_by_regime_fold,
        ["model", "scope", "dimension", "state", "feature"],
        "REGIME",
        minimum_sessions=spec.minimum_regime_sessions,
    )
    fold_interactions = pd.concat(interaction_frames, ignore_index=True)
    xgb_interactions = _aggregate_interactions(fold_interactions)
    stability = derive_explanation_stability(
        shap_by_fold,
        shap_by_regime,
        fold_interactions,
        spec=spec,
    )
    regime_status = str(_mapping(regime.get("status"), "regime status").get("regimeDiagnostics"))
    summary = derive_model_explanation_summary(
        shap_summary,
        fold_interactions,
        stability,
        spec=spec,
        regime_conditioning=regime_status,
    )
    revision = git_revision(Path(__file__).resolve().parents[3])
    contract = {
        "schemaVersion": EXPLANATION_STUDY_SCHEMA,
        "baseStudyId": base.get("studyId"),
        "baseStudyManifestSha256": sha256_file(base_path),
        "regimeStudyId": regime.get("studyId"),
        "regimeStudyManifestSha256": sha256_file(regime_path),
        "attributionStudyId": attribution.get("studyId"),
        "attributionStudyManifestSha256": sha256_file(attribution_path),
        "dataReleaseId": feature_contract.get("dataReleaseId"),
        "datasetVersionId": feature_contract.get("datasetVersionId"),
        "featureSnapshotId": feature_contract.get("featureSnapshotId"),
        "featureSnapshotManifestSha256": feature_contract.get("featureSnapshotManifestSha256"),
        "alphaPackId": feature_contract.get("alphaPackId"),
        "alphaPackSha256": feature_contract.get("alphaPackSha256"),
        "labelSpecId": feature_contract.get("labelSpecId"),
        "labelSpec": feature_contract.get("labelSpec"),
        "splitSpecSha256": feature_contract.get("splitSpecSha256"),
        "fullWalkForwardAcceptanceSha256": sha256_file(acceptance_path),
        "modelPredictionSha256": prediction_hashes,
        "taxonomyId": taxonomy.taxonomy_id,
        "taxonomySha256": taxonomy.semantic_sha256,
        "regimeDiagnosticsStatus": regime_status,
        "modelArtifactCertification": "DERIVED_SAME_RECORDER_ADDITIVITY",
        "foldModelInputs": model_inputs_contract,
        "explanationSpec": spec.to_manifest(),
        "studyImplementationSha256": {
            name: sha256_file(Path(__file__).resolve().parent / name)
            for name in ("model_explanation.py", "explanation_study.py")
        },
        "studyCodeCommit": revision.get("commit"),
        "studyCodeDirty": revision.get("dirty"),
        "modelTrainCalls": 0,
        "ordinaryModelPredictCalls": 0,
        "newPredictionArtifacts": 0,
        "featureMaterializationCalls": 0,
        "portfolioBacktestCalls": 0,
        "explanationEvaluationCalls": evaluation_calls,
        "selectionUsesFinalHoldout": False,
        "publishingAuthorized": False,
    }
    frames = {
        "feature_importance.parquet": feature_importance,
        "shap_summary.parquet": shap_summary,
        "shap_by_fold.parquet": shap_by_fold,
        "shap_by_year.parquet": shap_by_year,
        "shap_by_regime.parquet": shap_by_regime,
        "xgb_interactions.parquet": xgb_interactions,
        "explanation_stability.parquet": stability,
    }
    destination = (
        Path(output_root).expanduser().resolve()
        if output_root is not None
        else settings.paths.output / "research" / "alpha_phase1" / "explanation"
    )
    return _materialize_bundle(
        destination,
        contract=contract,
        frames=frames,
        summary=summary,
        regime_status=regime_status,
    )
