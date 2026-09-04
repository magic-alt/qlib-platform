from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.dataset as ds

from qlib_platform.research.feature_store import load_feature_store
from qlib_platform.lineage import git_revision, sha256_json
from qlib_platform.ops.platform_release import PlatformRelease, load_platform_release
from qlib_platform.settings import Settings
from qlib_platform.data.store import sha256_file
from qlib_platform.research.factor_taxonomy import FactorTaxonomy, load_factor_taxonomy
from qlib_platform.research.feature_diagnostics import feature_columns
from qlib_platform.research.regime import (
    REQUIRED_DIMENSIONS,
    RegimeSpec,
    build_regime_labels,
    load_regime_spec,
)
from qlib_platform.research.regime_diagnostics import RegimeDiagnosticArtifacts, build_regime_diagnostics
from qlib_platform.research.study import (
    _fold_assignments,
    _load_bound_features,
    _mapping,
    _validate_acceptance_and_run,
)


REGIME_STUDY_SCHEMA = "alpha_regime_study_v1"
REGIME_MANIFEST_NAME = "regime_diagnostics_manifest.json"


def _load_json(path: Path, name: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"{name} is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{name} must be a JSON object")
    return payload


def _validate_base_study(path: Path) -> dict[str, Any]:
    manifest = _load_json(path, "feature diagnostics study manifest")
    status = _mapping(manifest.get("status"), "feature diagnostics status")
    if status.get("featureDiagnostics") != "PASS":
        raise ValueError("base study Feature Diagnostics is not PASS")
    if manifest.get("selectionUsesFinalHoldout") is not False:
        raise ValueError("base study does not prove final-holdout isolation")
    if manifest.get("publishingAuthorized") is not False:
        raise ValueError("base study unexpectedly authorizes publishing")
    root = path.parent.resolve()
    for raw in manifest.get("artifacts", []):
        artifact = _mapping(raw, "base study artifact")
        target = (root / str(artifact.get("path") or "")).resolve()
        if target.parent != root or not target.is_file():
            raise ValueError(f"base study artifact is missing or escapes its root: {target}")
        if sha256_file(target) != artifact.get("sha256"):
            raise ValueError(f"base study artifact checksum mismatch: {target}")
    return manifest


def _artifact_path(manifest_path: Path, manifest: Mapping[str, Any], name: str) -> Path:
    matches = [raw for raw in manifest.get("artifacts", []) if raw.get("name") == name]
    if len(matches) != 1:
        raise ValueError(f"base study must contain exactly one {name}")
    path = (manifest_path.parent / str(matches[0]["path"])).resolve()
    if path.parent != manifest_path.parent.resolve():
        raise ValueError(f"base study artifact escapes its root: {path}")
    return path


def _load_model_predictions(
    acceptance: Mapping[str, Any],
    *,
    ridge_path: Path,
    lightgbm_path: Path,
    xgboost_path: Path,
) -> dict[str, pd.DataFrame]:
    models = _mapping(acceptance.get("models"), "acceptance models")
    paths = {"ridge": ridge_path, "lightgbm": lightgbm_path, "xgboost": xgboost_path}
    result: dict[str, pd.DataFrame] = {}
    for model, path in paths.items():
        expected = _mapping(models.get(model), f"accepted {model}").get("predictionSha256")
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"{model} rolling OOS prediction does not match acceptance")
        result[model] = pd.read_parquet(path)
    return result


def _load_benchmark_close(release: PlatformRelease) -> pd.Series:
    frame = pd.concat((pd.read_parquet(path) for path in release.files("benchmark")), ignore_index=True)
    if "ts_code" in frame:
        frame = frame.loc[frame["ts_code"].astype(str).str.upper().eq("000300.SH")]
    if not {"trade_date", "close"}.issubset(frame):
        raise ValueError("certified benchmark component requires trade_date and close")
    dates = pd.to_datetime(frame["trade_date"], errors="raise").dt.normalize()
    close = pd.Series(pd.to_numeric(frame["close"], errors="coerce").to_numpy(), index=dates)
    close = close.dropna().sort_index()
    if close.empty or close.index.has_duplicates:
        raise ValueError("certified benchmark close is empty or duplicated")
    return close


def _to_tushare_code(instrument: str) -> str:
    value = str(instrument).strip().upper()
    if len(value) == 8 and value[:2] in {"SH", "SZ", "BJ"} and value[2:].isdigit():
        return f"{value[2:]}.{value[:2]}"
    raise ValueError(f"invalid Qlib instrument: {instrument}")


def _to_qlib_instrument(code: str) -> str:
    value = str(code).strip().upper()
    if len(value) == 9 and value[6] == "." and value[6 + 1 :] in {"SH", "SZ", "BJ"}:
        return f"{value[7:]}{value[:6]}"
    raise ValueError(f"invalid Tushare instrument: {code}")


def _load_stock_returns(
    release: PlatformRelease,
    *,
    instruments: pd.Index,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.Series:
    codes = sorted({_to_tushare_code(str(value)) for value in instruments})
    files = [str(path) for path in release.files("bars")]
    dataset = ds.dataset(files, format="parquet")
    condition = (
        pc.field("ts_code").isin(codes)
        & (pc.field("trade_date") >= start.strftime("%Y%m%d"))
        & (pc.field("trade_date") <= end.strftime("%Y%m%d"))
    )
    table = dataset.to_table(columns=["ts_code", "trade_date", "pct_chg"], filter=condition)
    frame = table.to_pandas()
    if frame.empty:
        raise ValueError("certified bars contain no rows for regime stock returns")
    frame["datetime"] = pd.to_datetime(frame["trade_date"], errors="raise").dt.normalize()
    frame["instrument"] = frame["ts_code"].map(_to_qlib_instrument)
    frame["return"] = pd.to_numeric(frame["pct_chg"], errors="coerce") / 100.0
    result = frame.set_index(["datetime", "instrument"])["return"].sort_index()
    if result.index.has_duplicates:
        raise ValueError("certified bars contain duplicate regime return keys")
    return result


def _load_pit_industries(
    release: PlatformRelease,
    index: pd.MultiIndex,
) -> pd.Series | None:
    if "industry_classification_pit" not in release.components:
        return None
    intervals = pd.concat(
        (pd.read_parquet(path) for path in release.files("industry_classification_pit")),
        ignore_index=True,
    )
    required = {
        "instrument",
        "effective_from",
        "effective_to",
        "industry_code",
        "taxonomy",
        "level_no",
    }
    if not required.issubset(intervals):
        raise ValueError("PIT industry component has an incomplete schema")
    if (
        not intervals["taxonomy"].eq("SW2021").all()
        or not pd.to_numeric(intervals["level_no"], errors="raise").eq(1).all()
    ):
        raise ValueError("industry breadth requires PIT SW2021 level 1")
    intervals = intervals.copy()
    intervals["instrument"] = intervals["instrument"].astype(str).str.upper()
    intervals["effective_from"] = pd.to_datetime(intervals["effective_from"], errors="raise").dt.normalize()
    intervals["effective_to"] = pd.to_datetime(intervals["effective_to"], errors="raise").dt.normalize()
    key_frame = index.to_frame(index=False)
    key_frame["_order"] = np.arange(len(key_frame))
    merged = key_frame.merge(intervals, on="instrument", how="left", validate="many_to_many")
    valid = merged["datetime"].between(merged["effective_from"], merged["effective_to"])
    selected = merged.loc[valid, ["_order", "industry_code"]]
    if selected["_order"].duplicated().any():
        raise ValueError("overlapping PIT industry intervals entered regime diagnosis")
    result = pd.Series(float("nan"), index=np.arange(len(index)), dtype="object")
    result.loc[selected["_order"].to_numpy()] = selected["industry_code"].astype(str).to_numpy()
    result.index = index
    result.name = "industry"
    return result


def _history_start(
    benchmark_close: pd.Series,
    evaluation_dates: pd.DatetimeIndex,
    spec: RegimeSpec,
) -> pd.Timestamp:
    required = max(
        int(spec.dimensions["size_style"].get("window", 20))
        + int(spec.dimensions["size_style"].get("bucketLagSessions", 1)),
        int(spec.dimensions["industry_breadth"].get("window", 20)),
    )
    calendar = benchmark_close.index[benchmark_close.index < evaluation_dates.min()]
    if len(calendar) < required:
        raise ValueError("benchmark history is insufficient for causal regime warm-up")
    return pd.Timestamp(calendar[-required]).normalize()


def _implementation_hashes() -> dict[str, str]:
    root = Path(__file__).resolve().parent
    names = (
        "regime.py",
        "regime_diagnostics.py",
        "regime_study.py",
        "feature_diagnostics.py",
        "factor_taxonomy.py",
    )
    return {name: sha256_file(root / name) for name in names}


def _contract(
    *,
    base_study_path: Path,
    base_study: Mapping[str, Any],
    acceptance_path: Path,
    acceptance: Mapping[str, Any],
    release: PlatformRelease,
    feature_manifest: Mapping[str, Any],
    predictions: Mapping[str, Path],
    taxonomy: FactorTaxonomy,
    spec: RegimeSpec,
) -> dict[str, Any]:
    base_contract = _mapping(base_study.get("contract"), "base study contract")
    revision = git_revision(Path(__file__).resolve().parents[3])
    return {
        "schemaVersion": REGIME_STUDY_SCHEMA,
        "baseStudyId": base_study.get("studyId"),
        "baseStudyManifestSha256": sha256_file(base_study_path),
        "dataReleaseId": release.data_release_id,
        "dataReleaseManifestSha256": release.manifest_sha256,
        "datasetVersionId": base_contract.get("datasetVersionId"),
        "featureSnapshotId": feature_manifest.get("featureSnapshotId"),
        "featureSnapshotManifestSha256": base_contract.get("featureSnapshotManifestSha256"),
        "alphaPackId": base_contract.get("alphaPackId"),
        "alphaPackSha256": base_contract.get("alphaPackSha256"),
        "labelSpecId": base_contract.get("labelSpecId"),
        "labelSpec": base_contract.get("labelSpec"),
        "splitSpecSha256": base_contract.get("splitSpecSha256"),
        "fullWalkForwardAcceptanceSha256": sha256_file(acceptance_path),
        "modelPredictionSha256": {model: sha256_file(path) for model, path in sorted(predictions.items())},
        "taxonomyId": taxonomy.taxonomy_id,
        "taxonomySha256": taxonomy.semantic_sha256,
        "regimeSpec": spec.to_manifest(),
        "studyImplementationSha256": _implementation_hashes(),
        "studyCodeCommit": revision.get("commit"),
        "studyCodeDirty": revision.get("dirty"),
        "rawMaterializationCalls": 0,
        "selectionUsesFinalHoldout": False,
        "publishingAuthorized": False,
        "acceptedModels": sorted(_mapping(acceptance.get("models"), "acceptance models")),
    }


def _artifact_entry(path: Path, *, rows: int | None = None) -> dict[str, object]:
    result: dict[str, object] = {"name": path.name, "path": path.name, "sha256": sha256_file(path)}
    if rows is not None:
        result["rows"] = rows
    return result


def _availability(labels: pd.DataFrame, spec: RegimeSpec) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for dimension in REQUIRED_DIMENSIONS:
        block = labels.loc[labels["dimension"].eq(dimension)]
        available = block.loc[block["status"].eq("AVAILABLE")]
        counts = available.groupby("state")["date"].nunique().to_dict()
        result[dimension] = {
            "status": "AVAILABLE" if len(available) else "INPUT_UNAVAILABLE",
            "availableSessions": int(available["date"].nunique()),
            "stateSessions": {str(key): int(value) for key, value in sorted(counts.items())},
            "inferenceEligibleStates": sorted(
                str(key) for key, value in counts.items() if int(value) >= spec.minimum_sessions
            ),
        }
    return result


def _write_report(
    path: Path,
    *,
    study_id: str,
    availability: Mapping[str, Mapping[str, object]],
    artifacts: RegimeDiagnosticArtifacts,
    spec: RegimeSpec,
) -> None:
    regime_status = (
        "PASS" if all(value["status"] == "AVAILABLE" for value in availability.values()) else "PARTIAL"
    )
    lines = [
        "# Alpha Research Phase 1 — Causal Regime Diagnosis",
        "",
        f"- Study ID: `{study_id}`",
        f"- Regime Diagnostics: {regime_status}",
        "- Failure Attribution: NOT_RUN",
        "- Selection Uses Final Holdout: false",
        "- Publishing Authorized: false",
        "- Raw Materialization Calls: 0",
        "",
        "## Regime input availability",
        "",
        "| Dimension | Status | Available sessions | Inference-eligible states |",
        "| --- | --- | ---: | --- |",
    ]
    for dimension in REQUIRED_DIMENSIONS:
        value = availability[dimension]
        raw_eligible = value["inferenceEligibleStates"]
        eligible = (
            ", ".join(str(item) for item in raw_eligible) if isinstance(raw_eligible, list) else "none"
        ) or "none"
        lines.append(f"| {dimension} | {value['status']} | {value['availableSessions']} | {eligible} |")
    rolling_07 = artifacts.fold_profile.loc[artifacts.fold_profile["fold"].eq("rolling_07")]
    lines.extend(
        [
            "",
            "## rolling_07 causal profile",
            "",
            "The window is evaluated after globally applying the predeclared causal rules; it does not define a regime.",
            "",
            "| Dimension | State | Sessions | Ratio | Dominant |",
            "| --- | --- | ---: | ---: | --- |",
        ]
    )
    for row in rolling_07.itertuples(index=False):
        lines.append(
            f"| {row.dimension} | {row.state} | {row.sessions} | {row.session_ratio:.4f} | "
            f"{'yes' if row.dominant_state else 'no'} |"
        )
    factor = artifacts.factor_regime.loc[artifacts.factor_regime["sample_status"].eq("SUFFICIENT")].copy()
    factor["core"] = np.select(
        [
            factor["feature"].isin(spec.composites["value"]),
            factor["feature"].isin(spec.composites["low_vol"]),
        ],
        ["value", "low_vol"],
        default="other",
    )
    core = (
        factor.loc[factor["core"].ne("other")]
        .groupby(["dimension", "state", "sessions", "core"], sort=True)["oriented_rank_ic_mean"]
        .mean()
        .unstack("core")
        .reset_index()
    )
    lines.extend(
        [
            "",
            "## Table 1 — Economic cores by regime",
            "",
            "Core values are descriptive means of the pre-oriented member-factor RankICs. Full factor-level HAC and global BH-FDR results are in `factor_regime_diagnostics.parquet`.",
            "",
            "| Dimension | State | Sessions | Value core | Low-Vol core |",
            "| --- | --- | ---: | ---: | ---: |",
        ]
    )
    for row in core.itertuples(index=False):
        lines.append(
            f"| {row.dimension} | {row.state} | {row.sessions} | {row.value:.6f} | {row.low_vol:.6f} |"
        )
    model = artifacts.model_regime.loc[
        artifacts.model_regime["model"].isin(["ridge", "lightgbm", "xgboost", "xgboost_minus_lightgbm"])
        & artifacts.model_regime["sample_status"].eq("SUFFICIENT")
    ]
    model_table = model.pivot(
        index=["dimension", "state", "sessions"], columns="model", values="rank_ic_mean"
    ).reset_index()
    lines.extend(
        [
            "",
            "## Table 2 — Models by regime",
            "",
            "| Dimension | State | Sessions | Ridge | LightGBM | XGBoost | XGB-LGB |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in model_table.itertuples(index=False):
        lines.append(
            f"| {row.dimension} | {row.state} | {row.sessions} | {row.ridge:.6f} | "
            f"{row.lightgbm:.6f} | {row.xgboost:.6f} | {row.xgboost_minus_lightgbm:.6f} |"
        )
    correlation = artifacts.model_factor_correlation.loc[
        artifacts.model_factor_correlation["model"].eq("xgboost")
        & artifacts.model_factor_correlation["sample_status"].eq("SUFFICIENT")
    ]
    correlation_table = correlation.pivot(
        index=["dimension", "state", "sessions"],
        columns="composite",
        values="rank_correlation_mean",
    ).reset_index()
    lines.extend(
        [
            "",
            "## Table 3 — XGBoost score correlation with economic cores",
            "",
            "| Dimension | State | Sessions | Value | Low-Vol |",
            "| --- | --- | ---: | ---: | ---: |",
        ]
    )
    for row in correlation_table.itertuples(index=False):
        lines.append(
            f"| {row.dimension} | {row.state} | {row.sessions} | {row.value:.6f} | {row.low_vol:.6f} |"
        )
    overlap = artifacts.topk_overlap.loc[
        artifacts.topk_overlap["model"].eq("xgboost")
        & artifacts.topk_overlap["sample_status"].eq("SUFFICIENT")
    ]
    overlap_table = overlap.pivot(
        index=["dimension", "state", "sessions", "topk"],
        columns="composite",
        values="jaccard_mean",
    ).reset_index()
    lines.extend(
        [
            "",
            "## Table 4 — XGBoost TopK overlap with economic cores",
            "",
            "| Dimension | State | Sessions | K | Value Jaccard | Low-Vol Jaccard |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in overlap_table.itertuples(index=False):
        lines.append(
            f"| {row.dimension} | {row.state} | {row.sessions} | {row.topk} | "
            f"{row.value:.6f} | {row.low_vol:.6f} |"
        )
    lines.extend(
        [
            "",
            "This bundle provides candidate and model regime evidence only. It does not create AlphaPack v2, access the final holdout, or make a final Phase 1 recommendation.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _validate_existing(path: Path, contract: Mapping[str, Any]) -> Path:
    manifest_path = path / REGIME_MANIFEST_NAME
    manifest = _load_json(manifest_path, "existing regime diagnostics manifest")
    if manifest.get("contract") != dict(contract):
        raise ValueError(f"existing regime study contract differs: {path}")
    for raw in manifest.get("artifacts", []):
        artifact = _mapping(raw, "regime artifact")
        target = path / str(artifact.get("path") or "")
        if target.parent != path or not target.is_file() or sha256_file(target) != artifact.get("sha256"):
            raise ValueError(f"existing regime artifact checksum mismatch: {target}")
    return manifest_path


def _publish(
    output_root: Path,
    *,
    contract: dict[str, Any],
    diagnostics: RegimeDiagnosticArtifacts,
    spec: RegimeSpec,
) -> Path:
    study_id = "ard_" + sha256_json(contract)
    output_root.mkdir(parents=True, exist_ok=True)
    target = output_root / study_id
    if target.exists():
        return _validate_existing(target, contract)
    building = Path(tempfile.mkdtemp(prefix=f".{study_id}.", dir=output_root))
    try:
        frames = {
            "regime_labels.parquet": diagnostics.labels,
            "factor_regime_diagnostics.parquet": diagnostics.factor_regime,
            "model_regime_diagnostics.parquet": diagnostics.model_regime,
            "model_factor_regime_correlation.parquet": diagnostics.model_factor_correlation,
            "topk_regime_overlap.parquet": diagnostics.topk_overlap,
            "fold_regime_profile.parquet": diagnostics.fold_profile,
        }
        artifacts: list[dict[str, object]] = []
        for name, frame in frames.items():
            path = building / name
            frame.to_parquet(path, index=False)
            artifacts.append(_artifact_entry(path, rows=len(frame)))
        availability = _availability(diagnostics.labels, spec)
        definitions_path = building / "regime_definitions.json"
        definitions_path.write_text(
            json.dumps(
                {
                    "schemaVersion": REGIME_STUDY_SCHEMA,
                    "causality": {
                        "expandingThresholdAsOf": "T-1",
                        "sizeBucketLagSessions": spec.dimensions["size_style"].get("bucketLagSessions", 1),
                        "finalHoldoutUsed": False,
                    },
                    "spec": spec.to_manifest(),
                    "availability": availability,
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ),
            encoding="utf-8",
        )
        artifacts.append(_artifact_entry(definitions_path))
        report_path = building / "regime_diagnostics_report.md"
        _write_report(
            report_path,
            study_id=study_id,
            availability=availability,
            artifacts=diagnostics,
            spec=spec,
        )
        artifacts.append(_artifact_entry(report_path))
        complete = all(value["status"] == "AVAILABLE" for value in availability.values())
        manifest = {
            "schemaVersion": REGIME_STUDY_SCHEMA,
            "studyId": study_id,
            "studyType": "ALPHA_RESEARCH_PHASE1_CAUSAL_REGIME_DIAGNOSTICS",
            "contract": contract,
            "status": {
                "systemIntegrity": "PASS",
                "studyDeterminism": "PASS",
                "featureDiagnostics": "PASS",
                "regimeDiagnostics": "PASS" if complete else "PARTIAL",
                "failureAttribution": "NOT_RUN",
            },
            "availability": availability,
            "rawMaterializationCalls": 0,
            "selectionUsesFinalHoldout": False,
            "publishingAuthorized": False,
            "artifacts": artifacts,
        }
        manifest_path = building / REGIME_MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
        )
        try:
            os.replace(building, target)
        except OSError:
            if target.exists():
                return _validate_existing(target, contract)
            raise
        return target / REGIME_MANIFEST_NAME
    finally:
        if building.exists():
            shutil.rmtree(building, ignore_errors=True)


def run_regime_diagnose(
    settings: Settings,
    *,
    base_study: str | Path,
    acceptance: str | Path,
    walk_forward: str | Path,
    ridge_predictions: str | Path,
    lightgbm_predictions: str | Path,
    feature_snapshot: str | Path,
    taxonomy_path: str | Path,
    regime_path: str | Path,
    output_root: str | Path | None = None,
) -> Path:
    base_study_path = Path(base_study).expanduser().resolve()
    acceptance_path = Path(acceptance).expanduser().resolve()
    walk_forward_root = Path(walk_forward).expanduser().resolve()
    feature_root = Path(feature_snapshot).expanduser().resolve()
    ridge_path = Path(ridge_predictions).expanduser().resolve()
    lightgbm_path = Path(lightgbm_predictions).expanduser().resolve()
    base_manifest = _validate_base_study(base_study_path)
    accepted, run, selection_lock, labels, _ = _validate_acceptance_and_run(
        acceptance_path, walk_forward_root
    )
    raw_oos_features, feature_manifest = _load_bound_features(
        feature_root, run, accepted, selection_lock, labels
    )
    normalized_oos = feature_columns(raw_oos_features)
    base_contract = _mapping(base_manifest.get("contract"), "base study contract")
    if base_contract.get("featureSnapshotId") != feature_manifest.get("featureSnapshotId"):
        raise ValueError("base study and regime study FeatureSnapshot differ")
    if base_contract.get("dataReleaseId") != selection_lock.get("dataRelease"):
        raise ValueError("base study and regime study DataRelease differ")
    if base_contract.get("fullWalkForwardAcceptance", {}).get("sha256") != sha256_file(acceptance_path):
        raise ValueError("base study and regime study acceptance evidence differ")

    locked_alpha = _mapping(selection_lock.get("alphaPack"), "selection lock AlphaPack")
    taxonomy = load_factor_taxonomy(
        taxonomy_path,
        expected_features=list(normalized_oos.columns),
        expected_alpha_pack_id=str(locked_alpha.get("id") or ""),
    )
    spec = load_regime_spec(regime_path)
    if not set(spec.diagnostic_features).issubset(normalized_oos.columns):
        raise ValueError("regime candidate features are absent from the FeatureSnapshot")
    for feature in spec.hypothesis_features:
        if taxonomy.entry(feature).orientation is not None:
            raise ValueError(f"hypothesis-only feature direction must remain unknown: {feature}")

    release = load_platform_release(settings)
    if release.data_release_id != selection_lock.get("dataRelease"):
        raise ValueError("configured DataRelease differs from certified research selection lock")
    benchmark_close = _load_benchmark_close(release)
    evaluation_dates = (
        pd.DatetimeIndex(labels.index.get_level_values("datetime").unique()).normalize().sort_values()
    )
    history_start = _history_start(benchmark_close, evaluation_dates, spec)
    feature_start = str(
        _mapping(feature_manifest.get("coverage"), "FeatureSnapshot coverage").get("startTime")
    )
    history_features = feature_columns(
        load_feature_store(
            feature_root,
            feature_start,
            str(evaluation_dates.max().date()),
            verify_checksums=True,
        )
    )[["TURNOVER_F", "LOG_CIRC_MV"]]
    history_dates = history_features.index.get_level_values("datetime")
    regime_history_features = history_features.loc[
        history_dates.to_series(index=history_features.index).between(history_start, evaluation_dates.max())
    ]
    stock_returns = _load_stock_returns(
        release,
        instruments=regime_history_features.index.get_level_values("instrument").unique(),
        start=history_start,
        end=evaluation_dates.max(),
    )
    industry = _load_pit_industries(release, stock_returns.index)
    regime_labels = build_regime_labels(
        spec,
        benchmark_close=benchmark_close,
        features=history_features,
        stock_returns=stock_returns,
        industries=industry,
        evaluation_dates=evaluation_dates,
    )

    model_paths = {
        "ridge": ridge_path,
        "lightgbm": lightgbm_path,
        "xgboost": run.artifact("oos_predictions.parquet"),
    }
    predictions = _load_model_predictions(
        accepted,
        ridge_path=ridge_path,
        lightgbm_path=lightgbm_path,
        xgboost_path=model_paths["xgboost"],
    )
    feature_daily = pd.read_parquet(
        _artifact_path(base_study_path, base_manifest, "feature_daily_ic.parquet")
    )
    dates = pd.DatetimeIndex(labels.index.get_level_values("datetime").unique()).normalize().sort_values()
    assignments = _fold_assignments(selection_lock, dates)
    diagnostics = build_regime_diagnostics(
        regime_labels=regime_labels,
        feature_daily=feature_daily,
        features=normalized_oos.reindex(labels.index),
        labels=labels,
        predictions=predictions,
        taxonomy=taxonomy,
        spec=spec,
        fold_assignments=assignments,
    )
    contract = _contract(
        base_study_path=base_study_path,
        base_study=base_manifest,
        acceptance_path=acceptance_path,
        acceptance=accepted,
        release=release,
        feature_manifest=feature_manifest,
        predictions=model_paths,
        taxonomy=taxonomy,
        spec=spec,
    )
    destination = (
        Path(output_root).expanduser().resolve()
        if output_root is not None
        else settings.paths.output / "research" / "alpha_phase1" / "regime"
    )
    return _publish(destination, contract=contract, diagnostics=diagnostics, spec=spec)
