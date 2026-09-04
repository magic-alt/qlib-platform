from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from qlib_platform.research.feature_store import FEATURE_STORE_SCHEMA, load_feature_store
from qlib_platform.research.full_walk_forward_acceptance import RunEvidence
from qlib_platform.lineage import git_revision, sha256_json
from qlib_platform.artifacts.prediction_snapshot import load_prediction_snapshot
from qlib_platform.settings import Settings
from qlib_platform.data.store import sha256_file
from qlib_platform.research.factor_clusters import build_feature_clusters, mean_daily_rank_correlation
from qlib_platform.research.factor_taxonomy import FactorTaxonomy, load_factor_taxonomy
from qlib_platform.research.feature_diagnostics import (
    FeatureDiagnosticArtifacts,
    FeatureDiagnosticsSpec,
    build_feature_diagnostics,
    feature_columns,
    normalize_oos_labels,
)

STUDY_SCHEMA = "alpha_research_study_v1"
MANIFEST_NAME = "alpha_phase1_manifest.json"


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _load_json(path: Path, name: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"{name} is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{name} must be a JSON object")
    return payload


def _assert_equal(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise ValueError(f"{name} mismatch: {actual!r} != {expected!r}")


def _fold_assignments(
    selection_lock: Mapping[str, Any],
    oos_dates: pd.DatetimeIndex,
) -> dict[pd.Timestamp, str]:
    split = _mapping(selection_lock.get("splitSpec"), "selection lock splitSpec")
    folds = split.get("folds")
    if not isinstance(folds, list) or not folds:
        raise ValueError("selection lock contains no fold plan")
    assignments: dict[pd.Timestamp, str] = {}
    holdout_windows: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for raw_fold in folds:
        fold = _mapping(raw_fold, "fold")
        key = str(fold.get("key") or "").strip()
        test = fold.get("test")
        if not key or not isinstance(test, list) or len(test) != 2:
            raise ValueError("fold has an invalid key or test window")
        start, end = pd.Timestamp(test[0]).normalize(), pd.Timestamp(test[1]).normalize()
        is_holdout = bool(fold.get("final_holdout")) or key == "final_holdout"
        if is_holdout:
            holdout_windows.append((start, end))
            continue
        for date in oos_dates[(oos_dates >= start) & (oos_dates <= end)]:
            normalized = pd.Timestamp(date).normalize()
            if normalized in assignments:
                raise ValueError(f"rolling OOS date belongs to multiple folds: {normalized.date()}")
            assignments[normalized] = key
    missing = oos_dates.difference(pd.DatetimeIndex(assignments))
    if len(missing):
        raise ValueError(f"rolling OOS dates are outside the certified rolling folds: {missing[:5].tolist()}")
    leaked = [date for date in oos_dates if any(start <= date <= end for start, end in holdout_windows)]
    if leaked:
        raise ValueError(f"final holdout dates entered feature diagnosis: {leaked[:5]}")
    return assignments


def _validate_acceptance_and_run(
    acceptance_path: Path,
    run_root: Path,
) -> tuple[dict[str, Any], RunEvidence, dict[str, Any], pd.DataFrame, dict[str, Any]]:
    acceptance = _load_json(acceptance_path, "full walk-forward acceptance")
    if (
        acceptance.get("acceptanceType") != "FULL_WALK_FORWARD_V1"
        or acceptance.get("systemAcceptance") != "PASS"
        or acceptance.get("walkForwardAcceptance") != "PASS"
    ):
        raise ValueError("full walk-forward acceptance is not certified")
    run = RunEvidence.load(run_root)
    if str(run.evidence.get("model", {}).get("family") or "") != "xgboost":
        raise ValueError("alpha-diagnose requires the certified XGBoost walk-forward bundle")
    accepted_models = _mapping(acceptance.get("models"), "acceptance models")
    if set(accepted_models) != {"ridge", "lightgbm", "xgboost"}:
        raise ValueError("full walk-forward acceptance must contain exactly three certified models")
    accepted_xgb = _mapping(accepted_models.get("xgboost"), "accepted XGBoost")
    if _mapping(acceptance.get("determinism"), "acceptance determinism").get("xgboost") != "EXACT":
        raise ValueError("accepted XGBoost replay is not exact")
    accepted_holdout = _mapping(acceptance.get("finalHoldout"), "acceptance finalHoldout")
    if (
        accepted_holdout.get("isolated") is not True
        or accepted_holdout.get("usedForResearchSelection") is not False
    ):
        raise ValueError("full walk-forward acceptance does not prove final-holdout isolation")
    prediction_path = run.artifact("oos_predictions.parquet")
    _assert_equal(
        sha256_file(prediction_path),
        accepted_xgb.get("predictionSha256"),
        "accepted XGBoost rolling prediction SHA",
    )
    snapshot_frame, prediction_snapshot = load_prediction_snapshot(
        run.artifact("oos_predictions.snapshot.json")
    )
    if "label" not in snapshot_frame:
        raise ValueError("aggregate PredictionSnapshot does not embed certified rolling OOS labels")
    labels = normalize_oos_labels(pd.read_parquet(run.artifact("oos_labels.parquet")))
    if not labels.index.equals(snapshot_frame.index):
        raise ValueError("certified rolling predictions and labels do not have the exact same index")
    pd.testing.assert_frame_equal(
        labels,
        snapshot_frame[["label"]].reindex(labels.index),
        check_dtype=False,
        check_names=True,
    )
    oos_evidence = _mapping(run.evidence.get("oosPrediction"), "rolling OOS evidence")
    if int(oos_evidence.get("predictionDates", -1)) != int(
        labels.index.get_level_values("datetime").nunique()
    ):
        raise ValueError("rolling OOS session count differs from certified evidence")
    selection_lock = _mapping(run.evidence.get("researchSelectionLock"), "research selection lock")
    if (
        _mapping(selection_lock.get("finalHoldout"), "selection lock finalHoldout").get(
            "usedForResearchSelection"
        )
        is not False
    ):
        raise ValueError("certified selection lock does not seal the final holdout")
    acceptance_release = _mapping(acceptance.get("data"), "acceptance data").get("dataRelease")
    _assert_equal(selection_lock.get("dataRelease"), acceptance_release, "DataRelease")
    prediction_contract = _mapping(prediction_snapshot.get("contract"), "PredictionSnapshot contract")
    _assert_equal(
        prediction_contract.get("data_release_id"), acceptance_release, "PredictionSnapshot DataRelease"
    )
    _assert_equal(
        prediction_contract.get("feature_snapshot_id"),
        _mapping(acceptance.get("featureSnapshot"), "acceptance FeatureSnapshot").get("featureSnapshotId"),
        "PredictionSnapshot FeatureSnapshot",
    )
    _assert_equal(
        prediction_contract.get("label_spec_id"),
        _mapping(selection_lock.get("labelSpec"), "selection lock LabelSpec").get("id"),
        "PredictionSnapshot LabelSpec",
    )
    if (
        _mapping(acceptance.get("featureSnapshot"), "acceptance FeatureSnapshot").get(
            "rawMaterializationCalls"
        )
        != 0
        or _mapping(run.evidence.get("featureSnapshot"), "run FeatureSnapshot").get("rawMaterializationCalls")
        != 0
    ):
        raise ValueError("alpha diagnosis forbids raw feature materialization")
    return acceptance, run, dict(selection_lock), labels, prediction_snapshot


def _load_bound_features(
    feature_snapshot_root: Path,
    run: RunEvidence,
    acceptance: Mapping[str, Any],
    selection_lock: Mapping[str, Any],
    labels: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    manifest_path = feature_snapshot_root / "manifest.json"
    manifest = _load_json(manifest_path, "FeatureSnapshot manifest")
    if manifest.get("schemaVersion") != FEATURE_STORE_SCHEMA:
        raise ValueError(f"unsupported FeatureSnapshot schema: {manifest.get('schemaVersion')}")
    run_feature = _mapping(run.evidence.get("featureSnapshot"), "run FeatureSnapshot")
    accepted_feature = _mapping(acceptance.get("featureSnapshot"), "acceptance FeatureSnapshot")
    snapshot_id = manifest.get("featureSnapshotId")
    _assert_equal(snapshot_id, run_feature.get("featureSnapshotId"), "run FeatureSnapshot ID")
    _assert_equal(snapshot_id, accepted_feature.get("featureSnapshotId"), "accepted FeatureSnapshot ID")
    _assert_equal(
        sha256_file(manifest_path), run_feature.get("manifestSha256"), "FeatureSnapshot manifest SHA"
    )
    contract = _mapping(manifest.get("contract"), "FeatureSnapshot contract")
    alpha_pack = _mapping(contract.get("alphaPack"), "FeatureSnapshot AlphaPack")
    locked_alpha = _mapping(selection_lock.get("alphaPack"), "selection lock AlphaPack")
    _assert_equal(alpha_pack.get("pack_id"), locked_alpha.get("id"), "FeatureSnapshot AlphaPack ID")
    _assert_equal(
        alpha_pack.get("alpha_pack_sha256"),
        locked_alpha.get("sha256"),
        "FeatureSnapshot AlphaPack SHA",
    )
    _assert_equal(
        contract.get("datasetVersionId"),
        run_feature.get("datasetVersionId"),
        "FeatureSnapshot dataset version",
    )
    dates = pd.DatetimeIndex(labels.index.get_level_values("datetime")).normalize()
    frame = load_feature_store(
        feature_snapshot_root,
        str(dates.min().date()),
        str(dates.max().date()),
        verify_checksums=True,
    )
    return frame, manifest


def _implementation_hashes() -> dict[str, str]:
    root = Path(__file__).resolve().parent
    names = (
        "study.py",
        "feature_diagnostics.py",
        "factor_taxonomy.py",
        "factor_clusters.py",
    )
    return {name: sha256_file(root / name) for name in names}


def _study_contract(
    *,
    acceptance_path: Path,
    run: RunEvidence,
    selection_lock: Mapping[str, Any],
    feature_manifest: Mapping[str, Any],
    taxonomy: FactorTaxonomy,
    labels_path: Path,
    prediction_snapshot: Mapping[str, Any],
    spec: FeatureDiagnosticsSpec,
) -> dict[str, Any]:
    locked_alpha = _mapping(selection_lock.get("alphaPack"), "selection lock AlphaPack")
    locked_label = _mapping(selection_lock.get("labelSpec"), "selection lock LabelSpec")
    split = _mapping(selection_lock.get("splitSpec"), "selection lock splitSpec")
    run_feature = _mapping(run.evidence.get("featureSnapshot"), "run FeatureSnapshot")
    revision = git_revision(Path(__file__).resolve().parents[3])
    return {
        "schemaVersion": STUDY_SCHEMA,
        "dataReleaseId": selection_lock.get("dataRelease"),
        "datasetVersionId": run_feature.get("datasetVersionId"),
        "featureSnapshotId": feature_manifest.get("featureSnapshotId"),
        "featureSnapshotManifestSha256": run_feature.get("manifestSha256"),
        "alphaPackId": locked_alpha.get("id"),
        "alphaPackSha256": locked_alpha.get("sha256"),
        "labelSpecId": locked_label.get("id"),
        "labelSpec": locked_label.get("contract"),
        "splitSpecSha256": split.get("sha256"),
        "fullWalkForwardAcceptance": {
            "sha256": sha256_file(acceptance_path),
            "codeCommit": selection_lock.get("codeCommit"),
            "xgboostPredictionSha256": run.artifact_sha256("oos_predictions.parquet"),
        },
        "rollingOosLabelsSha256": sha256_file(labels_path),
        "predictionSnapshotId": prediction_snapshot.get("snapshotId"),
        "taxonomyId": taxonomy.taxonomy_id,
        "taxonomySha256": taxonomy.semantic_sha256,
        "taxonomyFileSha256": taxonomy.file_sha256,
        "diagnostics": spec.to_manifest(),
        "studyImplementationSha256": _implementation_hashes(),
        "studyCodeCommit": revision.get("commit"),
        "studyCodeDirty": revision.get("dirty"),
        "selectionUsesFinalHoldout": False,
        "publishingAuthorized": False,
    }


def _write_frame(frame: pd.DataFrame, path: Path) -> None:
    frame.to_parquet(path, index=False)


def _artifact_entry(path: Path, *, rows: int | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"name": path.name, "path": path.name, "sha256": sha256_file(path)}
    if rows is not None:
        payload["rows"] = rows
    return payload


def _write_report(
    path: Path,
    *,
    study_id: str,
    summary: pd.DataFrame,
    sessions: int,
    taxonomy: FactorTaxonomy,
) -> None:
    role_counts = {
        role: int(sum(entry.role == role for entry in taxonomy.entries.values()))
        for role in ("alpha", "exposure", "support")
    }
    eligible = summary.loc[summary["ranking_eligible"]]
    strongest = eligible.sort_values("oriented_rank_ic_mean", ascending=False, na_position="last").head(10)
    lines = [
        "# Alpha Research Phase 1 — Feature Diagnostics",
        "",
        f"- Study ID: `{study_id}`",
        f"- Rolling OOS sessions: {sessions}",
        f"- Features: {len(summary)}",
        f"- Roles: alpha={role_counts['alpha']}, exposure={role_counts['exposure']}, support={role_counts['support']}",
        "- Feature Diagnostics: PASS",
        "- Regime Diagnostics: NOT_RUN",
        "- Failure Attribution: NOT_RUN",
        "- Publishing Authorized: false",
        "",
        "## Highest pre-oriented mean RankIC (descriptive only)",
        "",
        "This table is diagnostic output, not a factor-selection or promotion decision.",
        "",
        "| Feature | Family | Oriented RankIC | Oriented RankICIR | Coverage median |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for row in strongest.itertuples(index=False):
        lines.append(
            f"| {row.feature} | {row.family} | {row.oriented_rank_ic_mean:.6f} | "
            f"{row.oriented_rank_icir:.6f} | {row.coverage_median:.6f} |"
        )
    lines.extend(
        [
            "",
            "No AlphaPack v2, StabilityScore, tier assignment, or final Phase 1 recommendation is produced by this foundation study.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _validate_existing(path: Path, contract: Mapping[str, Any]) -> Path:
    manifest_path = path / MANIFEST_NAME
    manifest = _load_json(manifest_path, "existing alpha research manifest")
    if manifest.get("contract") != dict(contract):
        raise ValueError(f"existing alpha research study contract differs: {path}")
    for artifact in manifest.get("artifacts", []):
        entry = _mapping(artifact, "study artifact")
        target = path / str(entry.get("path") or "")
        if target.parent != path or not target.is_file() or sha256_file(target) != entry.get("sha256"):
            raise ValueError(f"existing alpha research artifact checksum mismatch: {target}")
    return manifest_path


def _publish_study(
    output_root: Path,
    *,
    contract: dict[str, Any],
    diagnostics: FeatureDiagnosticArtifacts,
    correlations: pd.DataFrame,
    clusters: Mapping[str, Any],
    taxonomy: FactorTaxonomy,
) -> Path:
    study_id = "ars_" + sha256_json(contract)
    output_root.mkdir(parents=True, exist_ok=True)
    target = output_root / study_id
    if target.exists():
        return _validate_existing(target, contract)
    building = Path(tempfile.mkdtemp(prefix=f".{study_id}.", dir=output_root))
    try:
        frames = {
            "feature_daily_ic.parquet": diagnostics.daily,
            "feature_fold.parquet": diagnostics.fold,
            "feature_yearly.parquet": diagnostics.yearly,
            "feature_rolling_12m.parquet": diagnostics.rolling,
            "feature_summary.parquet": diagnostics.summary,
            "feature_correlation.parquet": correlations,
            "factor_quantile_returns.parquet": diagnostics.quantiles,
        }
        artifacts: list[dict[str, object]] = []
        for name, frame in frames.items():
            path = building / name
            _write_frame(frame, path)
            artifacts.append(_artifact_entry(path, rows=len(frame)))
        clusters_path = building / "feature_clusters.json"
        clusters_path.write_text(
            json.dumps(dict(clusters), ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
        )
        artifacts.append(_artifact_entry(clusters_path))
        sessions = int(diagnostics.daily["date"].nunique())
        report_path = building / "feature_diagnostics_report.md"
        _write_report(
            report_path,
            study_id=study_id,
            summary=diagnostics.summary,
            sessions=sessions,
            taxonomy=taxonomy,
        )
        artifacts.append(_artifact_entry(report_path))
        manifest = {
            "schemaVersion": STUDY_SCHEMA,
            "studyId": study_id,
            "studyType": "ALPHA_RESEARCH_PHASE1_FEATURE_DIAGNOSTICS",
            "contract": contract,
            "status": {
                "systemIntegrity": "PASS",
                "studyDeterminism": "PASS",
                "featureDiagnostics": "PASS",
                "regimeDiagnostics": "NOT_RUN",
                "failureAttribution": "NOT_RUN",
            },
            "featureCount": len(diagnostics.summary),
            "rollingOosSessions": sessions,
            "rawMaterializationCalls": 0,
            "selectionUsesFinalHoldout": False,
            "publishingAuthorized": False,
            "taxonomy": taxonomy.to_manifest(),
            "artifacts": artifacts,
        }
        manifest_path = building / MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
        )
        try:
            os.replace(building, target)
        except OSError:
            if target.exists():
                return _validate_existing(target, contract)
            raise
        return target / MANIFEST_NAME
    finally:
        if building.exists():
            shutil.rmtree(building, ignore_errors=True)


def run_alpha_diagnose(
    settings: Settings,
    *,
    acceptance: str | Path,
    walk_forward: str | Path,
    feature_snapshot: str | Path,
    taxonomy_path: str | Path,
    output_root: str | Path | None = None,
    spec: FeatureDiagnosticsSpec | None = None,
) -> Path:
    acceptance_path = Path(acceptance).expanduser().resolve()
    run_root = Path(walk_forward).expanduser().resolve()
    feature_root = Path(feature_snapshot).expanduser().resolve()
    selected_spec = spec or FeatureDiagnosticsSpec()
    accepted, run, selection_lock, labels, prediction_snapshot = _validate_acceptance_and_run(
        acceptance_path, run_root
    )
    raw_features, feature_manifest = _load_bound_features(
        feature_root,
        run,
        accepted,
        selection_lock,
        labels,
    )
    normalized = feature_columns(raw_features)
    locked_alpha = _mapping(selection_lock.get("alphaPack"), "selection lock AlphaPack")
    taxonomy = load_factor_taxonomy(
        taxonomy_path,
        expected_features=list(normalized.columns),
        expected_alpha_pack_id=str(locked_alpha.get("id") or ""),
    )
    dates = pd.DatetimeIndex(labels.index.get_level_values("datetime").unique()).normalize().sort_values()
    assignments = _fold_assignments(selection_lock, dates)
    label_contract = _mapping(
        _mapping(selection_lock.get("labelSpec"), "selection lock LabelSpec").get("contract"),
        "LabelSpec contract",
    )
    hac_lag = int(label_contract.get("lookahead_days", 0))
    if hac_lag < 1:
        raise ValueError("LabelSpec lookahead_days must be positive for HAC diagnostics")
    diagnostics = build_feature_diagnostics(
        raw_features,
        labels,
        taxonomy,
        selected_spec,
        fold_assignments=assignments,
        hac_lag=hac_lag,
    )
    aligned = normalized.reindex(labels.index)
    correlations = mean_daily_rank_correlation(
        aligned,
        min_cross_section=selected_spec.min_cross_section,
    )
    clusters = build_feature_clusters(
        correlations,
        taxonomy,
        threshold=selected_spec.correlation_threshold,
    )
    contract = _study_contract(
        acceptance_path=acceptance_path,
        run=run,
        selection_lock=selection_lock,
        feature_manifest=feature_manifest,
        taxonomy=taxonomy,
        labels_path=run.artifact("oos_labels.parquet"),
        prediction_snapshot=prediction_snapshot,
        spec=selected_spec,
    )
    destination = (
        Path(output_root).expanduser().resolve()
        if output_root is not None
        else settings.paths.output / "research" / "alpha_phase1"
    )
    return _publish_study(
        destination,
        contract=contract,
        diagnostics=diagnostics,
        correlations=correlations,
        clusters=clusters,
        taxonomy=taxonomy,
    )
