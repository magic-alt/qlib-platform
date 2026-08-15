from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .settings import Settings
from .model_runtime import load_model_profile, resolve_runtime, write_timings
from .research_gate import (
    derive_daily_signal_diagnostics,
    ResearchThresholds,
    derive_research_metrics,
    evaluate_research_metrics,
    write_gate_report,
)
from .store import sha256_file
from .lineage import dirty_research_override_enabled, git_revision, resolve_qlib_repo, sha256_json
from .universe import membership_fingerprint
from .research_timing import effective_label_gap, label_timing_from_settings, shared_research_calendar
from .train_select import _research_label_horizon_days, train_backtest_select
from .canonical_config import StrategySpec
from .feature_store import feature_store_enabled, prepare_feature_data
from .dataset_resolver import pin_dataset
from .prediction_backtest import backtest_predictions
from .prediction_snapshot import (
    PredictionSnapshotSpec,
    load_prediction_snapshot,
    prediction_snapshot_path,
    write_prediction_snapshot,
)
from .walk_forward_acceptance import (
    performance_baseline,
    validate_fold_integrity,
    validate_processor_isolation,
)


@dataclass(frozen=True)
class Fold:
    key: str
    train: tuple[str, str]
    valid: tuple[str, str]
    test: tuple[str, str]
    final_holdout: bool = False


@dataclass(frozen=True)
class CheckpointValidation:
    manifest_path: Path | None
    status: str
    reason: str | None = None


def _artifact_path(manifest: dict[str, Any], name: str) -> Path:
    for item in manifest.get("artifacts", []):
        if isinstance(item, dict) and item.get("name") == name and item.get("localPath"):
            return Path(str(item["localPath"]))
    raise FileNotFoundError(f"manifest artifact is missing: {name}")


def _write_research_selection_lock(
    path: Path,
    *,
    fold_plan: list[Fold],
    rolling_manifests: list[dict[str, Any]],
    aggregate_prediction_sha256: str,
    thresholds: ResearchThresholds,
) -> dict[str, object]:
    if not rolling_manifests:
        raise ValueError("research selection lock requires rolling OOS evidence")
    reference = rolling_manifests[0]
    experiment = reference.get("researchExperiment")
    if not isinstance(experiment, dict):
        raise ValueError("rolling fold is missing its research experiment contract")
    project_root = Path(__file__).resolve().parents[2]
    code_revision = git_revision(project_root)
    payload: dict[str, object] = {
        "schemaVersion": "research_selection_lock_v1",
        "dataRelease": experiment.get("data_release_id"),
        "alphaPack": {
            "id": experiment.get("alpha_pack_id"),
            "sha256": experiment.get("alpha_pack_sha256"),
        },
        "labelSpec": {
            "id": experiment.get("label_spec_id"),
            "contract": experiment.get("label"),
        },
        "splitSpec": {
            "profile": experiment.get("split_profile_id"),
            "folds": [asdict(fold) for fold in fold_plan],
            "sha256": sha256_json([asdict(fold) for fold in fold_plan]),
        },
        "modelProfile": {
            "id": experiment.get("model_profile_id"),
            "sha256": experiment.get("model_profile_sha256"),
        },
        "portfolioPolicy": {
            "id": experiment.get("portfolio_policy_id"),
            "sha256": experiment.get("portfolio_policy_sha256"),
        },
        "gateThresholds": asdict(thresholds),
        "codeCommit": code_revision.get("commit"),
        "codeDirty": code_revision.get("dirty"),
        "rollingOosPredictionSha256": aggregate_prediction_sha256,
        "rollingOOS": {"usedForResearchSelection": True},
        "finalHoldout": {
            "usedForResearchSelection": False,
            "accessedBeforeFinalization": False,
        },
    }
    payload["lockSha256"] = sha256_json(payload)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return payload


def _aggregate_component_timings(manifests: list[dict[str, Any]]) -> dict[str, float]:
    aggregate: dict[str, float] = {}
    for manifest in manifests:
        timings = manifest.get("timings", {})
        phases = timings.get("phasesSeconds", {}) if isinstance(timings, dict) else {}
        if not isinstance(phases, dict):
            continue
        for key, value in phases.items():
            aggregate[str(key)] = aggregate.get(str(key), 0.0) + float(value)
    return {key: round(value, 6) for key, value in aggregate.items()}


def _read_oos_frame(manifest: dict[str, Any], name: str, column: str) -> pd.DataFrame:
    frame = pd.read_parquet(_artifact_path(manifest, name))
    if isinstance(frame, pd.Series):
        frame = frame.to_frame(column)
    if not isinstance(frame.index, pd.MultiIndex) or not {
        "datetime",
        "instrument",
    }.issubset(frame.index.names):
        raise ValueError(f"{name} must use a datetime/instrument MultiIndex")
    if column not in frame:
        if len(frame.columns) != 1:
            raise ValueError(f"{name} must contain a {column} column")
        frame = frame.rename(columns={frame.columns[0]: column})
    frame = frame[[column]]
    if not frame.index.is_monotonic_increasing:
        raise ValueError(f"{name} contains out-of-order datetime/instrument rows")
    return frame


def _write_continuous_oos_stream(
    manifests: list[dict[str, Any]],
    output_dir: Path,
    *,
    expected_dates: pd.DatetimeIndex | None = None,
) -> tuple[Path, Path, dict[str, object]]:
    """Persist one strictly ordered prediction/label stream from rolling folds."""

    if not manifests:
        raise ValueError("continuous OOS stream requires rolling component manifests")
    output_dir.mkdir(parents=True, exist_ok=True)
    predictions: list[pd.DataFrame] = []
    labels: list[pd.DataFrame] = []
    components: list[dict[str, object]] = []
    snapshot_contracts: list[dict[str, str]] = []
    previous_end: pd.Timestamp | None = None
    for manifest in manifests:
        pred = _read_oos_frame(manifest, "oos_predictions.parquet", "score")
        label = _read_oos_frame(manifest, "oos_labels.parquet", "label")
        if not pred.index.equals(label.index):
            raise ValueError("fold prediction and label indexes do not match exactly")
        pred_dates = pd.DatetimeIndex(pred.index.get_level_values("datetime")).normalize()
        start = pred_dates.min()
        end = pred_dates.max()
        if previous_end is not None and start <= previous_end:
            raise ValueError(
                "rolling OOS prediction dates overlap or are out of order: "
                f"{start.date()} <= {previous_end.date()}"
            )
        previous_end = end
        predictions.append(pred)
        labels.append(label)
        declared_snapshot = manifest.get("predictionSnapshot")
        if isinstance(declared_snapshot, dict):
            _, verified_snapshot = load_prediction_snapshot(
                _artifact_path(manifest, "oos_predictions.snapshot.json")
            )
            if verified_snapshot.get("snapshotId") != declared_snapshot.get("snapshotId"):
                raise ValueError("fold prediction snapshot does not match its run manifest")
            contract = verified_snapshot.get("contract")
            if not isinstance(contract, dict):
                raise ValueError("fold prediction snapshot contract is missing")
            snapshot_contracts.append({str(key): str(value) for key, value in contract.items()})
        components.append(
            {
                "externalRunId": str(manifest.get("externalRunId", "")),
                "startDate": str(start.date()),
                "endDate": str(end.date()),
                "predictionRows": len(pred),
                "labelRows": len(label),
            }
        )
    combined_predictions = pd.concat(predictions).sort_index()
    combined_labels = pd.concat(labels).sort_index()
    if combined_predictions.index.has_duplicates:
        raise ValueError("continuous OOS predictions contain duplicate datetime/instrument rows")
    if combined_labels.index.has_duplicates:
        raise ValueError("continuous OOS labels contain duplicate datetime/instrument rows")
    actual_dates = pd.DatetimeIndex(
        combined_predictions.index.get_level_values("datetime").unique()
    ).normalize()
    if expected_dates is not None:
        expected = pd.DatetimeIndex(expected_dates).normalize().drop_duplicates().sort_values()
        missing = expected.difference(actual_dates)
        unexpected = actual_dates.difference(expected)
        if len(missing) or len(unexpected):
            raise ValueError(
                "rolling OOS prediction calendar mismatch: "
                f"missing={list(map(str, missing.date[:5]))}, "
                f"unexpected={list(map(str, unexpected.date[:5]))}"
            )
    prediction_path = output_dir / "oos_predictions.parquet"
    label_path = output_dir / "oos_labels.parquet"
    aggregate_snapshot: dict[str, object] | None = None
    if snapshot_contracts:
        if len(snapshot_contracts) != len(manifests):
            raise ValueError("rolling OOS components mix governed and legacy prediction artifacts")
        stable_fields = (
            "data_release_id",
            "alpha_pack_id",
            "feature_snapshot_id",
            "label_spec_id",
            "model_profile_id",
        )
        first = snapshot_contracts[0]
        drift = [
            field
            for field in stable_fields
            if any(contract[field] != first[field] for contract in snapshot_contracts[1:])
        ]
        if drift:
            raise ValueError(f"rolling OOS prediction snapshot contract drift: {drift}")
        aggregate_snapshot = write_prediction_snapshot(
            prediction_path,
            combined_predictions,
            labels=combined_labels,
            spec=PredictionSnapshotSpec(
                data_release_id=first["data_release_id"],
                alpha_pack_id=first["alpha_pack_id"],
                feature_snapshot_id=first["feature_snapshot_id"],
                label_spec_id=first["label_spec_id"],
                split_spec_id="wf_" + sha256_json([item["split_spec_id"] for item in snapshot_contracts]),
                model_id="wf_" + sha256_json([item["model_id"] for item in snapshot_contracts]),
                model_profile_id=first["model_profile_id"],
                fold_id="rolling_oos_aggregate",
            ),
        )
    else:
        combined_predictions.to_parquet(prediction_path)
    combined_labels.to_parquet(label_path)
    instrument_counts = combined_predictions.groupby(level="datetime").size().sort_index()
    aggregate_sha256 = sha256_file(prediction_path)
    metadata: dict[str, object] = {
        "componentCount": len(components),
        "components": components,
        "startDate": components[0]["startDate"],
        "endDate": components[-1]["endDate"],
        "predictionRows": len(combined_predictions),
        "labelRows": len(combined_labels),
        "predictionDates": int(combined_predictions.index.get_level_values("datetime").nunique()),
        "duplicateRows": 0,
        "overlappingTestDates": 0,
        "outOfOrderRows": 0,
        "missingExpectedFoldDates": 0,
        "aggregatePredictionSha256": aggregate_sha256,
        "instrumentCountPerDay": {
            str(pd.Timestamp(date).date()): int(value) for date, value in instrument_counts.items()
        },
        "instrumentCountMin": int(instrument_counts.min()),
        "instrumentCountMax": int(instrument_counts.max()),
        "predictionSnapshot": aggregate_snapshot,
    }
    return prediction_path, label_path, metadata


def _verify_fold_boundary_continuity(
    manifests: list[dict[str, Any]],
    holdings: pd.DataFrame,
    audit: pd.DataFrame,
    report: pd.DataFrame | None = None,
    *,
    initial_cash: float | None = None,
) -> dict[str, object]:
    """Verify untouched positions do not lose their holding age at model boundaries."""

    required = {"trade_date", "instrument", "quantity", "holding_days"}
    missing = required - set(holdings.columns)
    if missing:
        raise ValueError(f"continuous holdings are missing columns: {sorted(missing)}")
    frame = holdings.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.normalize()
    frame["instrument"] = frame["instrument"].astype(str)
    frame["holding_days"] = pd.to_numeric(frame["holding_days"], errors="raise")
    audit_frame = audit.copy()
    if not audit_frame.empty and {"trade_date", "instrument"}.issubset(audit_frame.columns):
        audit_frame["trade_date"] = pd.to_datetime(audit_frame["trade_date"]).dt.normalize()
        audit_frame["instrument"] = audit_frame["instrument"].astype(str)
    results: list[dict[str, object]] = []
    unexpected_resets: list[dict[str, object]] = []
    cash_resets: list[dict[str, object]] = []
    report_frame = report.copy() if report is not None else pd.DataFrame()
    if not report_frame.empty:
        report_frame.index = pd.DatetimeIndex(pd.to_datetime(report_frame.index)).normalize()
        if "account" not in report_frame or "cash" not in report_frame:
            raise ValueError("continuous portfolio report must contain account and cash")
    for previous, current in zip(manifests, manifests[1:], strict=False):
        current_pred = _read_oos_frame(current, "oos_predictions.parquet", "score")
        boundary = pd.DatetimeIndex(current_pred.index.get_level_values("datetime")).normalize().min()
        prior_dates = frame.loc[frame["trade_date"] < boundary, "trade_date"]
        next_dates = frame.loc[frame["trade_date"] >= boundary, "trade_date"]
        base = {
            "previousRunId": str(previous.get("externalRunId", "")),
            "currentRunId": str(current.get("externalRunId", "")),
            "boundarySignalDate": str(boundary.date()),
        }
        reset_reasons: list[str] = []
        if not report_frame.empty:
            prior_report_dates = report_frame.index[report_frame.index < boundary]
            next_report_dates = report_frame.index[report_frame.index >= boundary]
            if len(prior_report_dates) and len(next_report_dates):
                before_report = report_frame.loc[prior_report_dates.max()]
                after_report = report_frame.loc[next_report_dates.min()]
                for cumulative in ("total_turnover", "total_cost"):
                    if cumulative in report_frame and float(after_report[cumulative]) + 1e-9 < float(
                        before_report[cumulative]
                    ):
                        reset_reasons.append(f"{cumulative}_decreased")
                if initial_cash is not None:
                    tolerance = max(1e-6, abs(initial_cash) * 1e-9)
                    after_is_initial = (
                        abs(float(after_report["account"]) - initial_cash) <= tolerance
                        and abs(float(after_report["cash"]) - initial_cash) <= tolerance
                    )
                    before_is_initial = (
                        abs(float(before_report["account"]) - initial_cash) <= tolerance
                        and abs(float(before_report["cash"]) - initial_cash) <= tolerance
                    )
                    if after_is_initial and not before_is_initial:
                        reset_reasons.append("account_and_cash_returned_to_initial_state")
        if reset_reasons:
            cash_resets.append({**base, "reasons": reset_reasons})
        if prior_dates.empty or next_dates.empty:
            results.append(
                {
                    **base,
                    "status": "NO_COMPARABLE_HOLDING_SNAPSHOTS" if not reset_reasons else "FAIL",
                    "continuingPositions": 0,
                    "unexpectedCashResets": reset_reasons,
                }
            )
            continue
        prior_date = prior_dates.max()
        next_date = next_dates.min()
        before = frame.loc[frame["trade_date"].eq(prior_date)].set_index("instrument")
        after = frame.loc[frame["trade_date"].eq(next_date)].set_index("instrument")
        continuing = before.index.intersection(after.index)
        traded: set[str] = set()
        if not audit_frame.empty and {"trade_date", "instrument"}.issubset(audit_frame.columns):
            action_column = "actual_action" if "actual_action" in audit_frame else "target_action"
            if action_column in audit_frame:
                traded_rows = audit_frame.loc[
                    audit_frame["trade_date"].gt(prior_date)
                    & audit_frame["trade_date"].le(next_date)
                    & audit_frame[action_column].isin(["BUY", "SELL"])
                ]
                traded = set(traded_rows["instrument"])
        untouched = [instrument for instrument in continuing if instrument not in traded]
        resets = [
            {
                "instrument": str(instrument),
                "beforeHoldingDays": int(before.at[instrument, "holding_days"]),
                "afterHoldingDays": int(after.at[instrument, "holding_days"]),
            }
            for instrument in untouched
            if int(after.at[instrument, "holding_days"]) < int(before.at[instrument, "holding_days"])
        ]
        unexpected_resets.extend(resets)
        results.append(
            {
                **base,
                "previousHoldingDate": str(prior_date.date()),
                "currentHoldingDate": str(next_date.date()),
                "status": "PASS" if not resets else "FAIL",
                "continuingPositions": len(continuing),
                "untouchedContinuingPositions": len(untouched),
                "unexpectedHoldingDayResets": resets,
                "unexpectedCashResets": reset_reasons,
            }
        )
    if unexpected_resets:
        raise RuntimeError(
            f"fold boundary holding_days reset in continuous backtest: {unexpected_resets[:5]}"
        )
    if cash_resets:
        raise RuntimeError(f"fold boundary cash/account reset in continuous backtest: {cash_resets[:5]}")
    return {
        "passed": True,
        "portfolioState": "SINGLE_CONTINUOUS_ACCOUNT",
        "portfolioBacktestRunCount": 1,
        "portfolioInitialCashEventCount": 1,
        "boundaryCount": len(results),
        "boundaries": results,
        "unexpectedHoldingDayResetCount": 0,
        "boundaryHoldingResetCount": 0,
        "boundaryCashResetCount": 0,
    }


def _training_checkpoint_fingerprint(
    settings: Settings,
    fold: Fold,
    *,
    runtime_fingerprint: str,
) -> str:
    dataset_manifest = settings.qlib_data_uri / "dataset_manifest.json"
    project_root = Path(__file__).resolve().parents[2]
    source_files = [
        Path(__file__),
        project_root / "src" / "tushare_qlib" / "custom_handler.py",
        project_root / "src" / "tushare_qlib" / "processors.py",
        project_root / "src" / "tushare_qlib" / "research_timing.py",
        project_root / "src" / "tushare_qlib" / "model_runtime.py",
        project_root / "src" / "tushare_qlib" / "processor_state.py",
        project_root / "src" / "tushare_qlib" / "train_select.py",
        project_root / "src" / "tushare_qlib" / "prediction_snapshot.py",
        project_root / "src" / "tushare_qlib" / "walk_forward_acceptance.py",
    ]
    research = settings.data.get("research", {})
    research = research if isinstance(research, dict) else {}
    payload = {
        "runtimeFingerprint": runtime_fingerprint,
        "fold": asdict(fold),
        "modelResearch": {
            key: research.get(key)
            for key in (
                "random_seed",
                "num_threads",
                "label_horizon_days",
                "feature_store",
            )
        },
        "universe": settings.data.get("universe", {}),
        "datasetUri": str(settings.qlib_data_uri),
        "datasetManifestSha256": sha256_file(dataset_manifest) if dataset_manifest.is_file() else None,
        "universeMembershipSha256": membership_fingerprint(settings),
        "qlibCommit": git_revision(resolve_qlib_repo(settings.qlib_repo)).get("commit"),
        "featureImplementationSha256": {
            path.name: sha256_file(path) for path in source_files if path.is_file()
        },
        "labelHorizonDays": _research_label_horizon_days(settings),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def _portfolio_checkpoint_fingerprint(
    settings: Settings,
    *,
    source_prediction_fingerprint: str,
    benchmark: str,
    topn: int | None,
) -> str:
    research = settings.data.get("research", {})
    research = research if isinstance(research, dict) else {}
    strategy = asdict(StrategySpec.from_settings(settings, topk_override=topn))
    payload = {
        "sourcePredictionFingerprint": source_prediction_fingerprint,
        "benchmark": benchmark,
        "strategy": strategy,
        "backtestResearch": {
            key: research.get(key)
            for key in (
                "backtest_account",
                "deal_price",
                "max_participation_rate",
                "trade_unit",
                "open_cost",
                "close_cost",
                "min_cost",
                "signal_lag_days",
            )
        },
        "portfolioImplementationSha256": sha256_file(
            Path(__file__).resolve().parents[2] / "src" / "tushare_qlib" / "prediction_backtest.py"
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def _checkpoint_fingerprint(
    settings: Settings,
    fold: Fold,
    *,
    runtime_fingerprint: str,
    benchmark: str,
    topn: int | None,
) -> str:
    training = _training_checkpoint_fingerprint(
        settings,
        fold,
        runtime_fingerprint=runtime_fingerprint,
    )
    portfolio = _portfolio_checkpoint_fingerprint(
        settings,
        source_prediction_fingerprint=training,
        benchmark=benchmark,
        topn=topn,
    )
    payload = {
        "trainingFingerprint": training,
        "portfolioFingerprint": portfolio,
        "promotionContract": "final-holdout-v1" if fold.final_holdout else "component-validation-v3",
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[
        :16
    ]


def _checkpoint_payload(manifest_path: Path, expected_fingerprint: str) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts: list[dict[str, str]] = []
    for artifact in manifest.get("artifacts", []):
        if not isinstance(artifact, dict):
            raise ValueError("checkpoint manifest contains an invalid artifact entry")
        path = Path(str(artifact.get("localPath") or "")).resolve()
        if not path.is_file():
            raise ValueError(f"checkpoint artifact is not a file: {path}")
        artifacts.append(
            {
                "name": str(artifact.get("name") or path.name),
                "localPath": str(path),
                "sha256": sha256_file(path),
            }
        )
    if not artifacts:
        raise ValueError("checkpoint manifest contains no reusable artifacts")
    return {
        "schemaVersion": "walk_forward_checkpoint_v2",
        "manifest": str(manifest_path.resolve()),
        "manifestSha256": sha256_file(manifest_path),
        "checkpointFingerprint": expected_fingerprint,
        "artifacts": artifacts,
    }


def _inspect_checkpoint(
    settings: Settings, checkpoint: Path, expected_fingerprint: str
) -> CheckpointValidation:
    if not checkpoint.is_file():
        return CheckpointValidation(None, "MISSING")
    try:
        payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return CheckpointValidation(None, "CORRUPTED", f"checkpoint_json:{type(exc).__name__}")
    if payload.get("schemaVersion") != "walk_forward_checkpoint_v2":
        return CheckpointValidation(None, "STALE", "checkpoint_schema")
    if payload.get("checkpointFingerprint") != expected_fingerprint:
        return CheckpointValidation(None, "STALE", "checkpoint_fingerprint")
    try:
        manifest_path = Path(str(payload["manifest"])).resolve()
        if not manifest_path.is_file():
            return CheckpointValidation(None, "CORRUPTED", "manifest_missing")
        if sha256_file(manifest_path) != payload.get("manifestSha256"):
            return CheckpointValidation(None, "CORRUPTED", "manifest_sha256")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return CheckpointValidation(None, "CORRUPTED", f"manifest:{type(exc).__name__}")
    dataset_manifest = settings.qlib_data_uri / "dataset_manifest.json"
    if dataset_manifest.is_file():
        current_dataset = json.loads(dataset_manifest.read_text(encoding="utf-8"))
        current_fingerprint = str(
            current_dataset.get("sha256", current_dataset.get("dataset_id", "unversioned"))
        )
        if str(manifest.get("dataset", {}).get("fingerprint")) != current_fingerprint:
            return CheckpointValidation(None, "STALE", "dataset_fingerprint")
    lineage = manifest.get("lineage", {})
    if not isinstance(lineage, dict) or not lineage.get("lineageId"):
        return CheckpointValidation(None, "CORRUPTED", "lineage_missing")
    if not lineage.get("complete") and not dirty_research_override_enabled(settings, lineage):
        return CheckpointValidation(None, "STALE", "lineage_incomplete")
    recorded_artifacts = payload.get("artifacts")
    if not isinstance(recorded_artifacts, list) or not recorded_artifacts:
        return CheckpointValidation(None, "CORRUPTED", "artifact_hashes_missing")
    current_paths = {
        str(Path(str(item.get("localPath") or "")).resolve())
        for item in manifest.get("artifacts", [])
        if isinstance(item, dict)
    }
    recorded_paths: set[str] = set()
    for artifact in recorded_artifacts:
        if not isinstance(artifact, dict):
            return CheckpointValidation(None, "CORRUPTED", "artifact_hash_entry")
        path = Path(str(artifact.get("localPath") or "")).resolve()
        recorded_paths.add(str(path))
        if not path.is_file() or sha256_file(path) != artifact.get("sha256"):
            return CheckpointValidation(None, "CORRUPTED", f"artifact_sha256:{path.name}")
    if recorded_paths != current_paths:
        return CheckpointValidation(None, "CORRUPTED", "artifact_set")
    return CheckpointValidation(manifest_path, "VALID")


def _validated_checkpoint_manifest(
    settings: Settings, checkpoint: Path, expected_fingerprint: str
) -> Path | None:
    return _inspect_checkpoint(settings, checkpoint, expected_fingerprint).manifest_path


def _at_or_after(calendar: pd.DatetimeIndex, value: pd.Timestamp) -> pd.Timestamp:
    found = calendar[calendar >= value]
    if found.empty:
        raise ValueError(f"no trading day at or after {value.date()}")
    return found[0]


def _at_or_before(calendar: pd.DatetimeIndex, value: pd.Timestamp) -> pd.Timestamp:
    found = calendar[calendar <= value]
    if found.empty:
        raise ValueError(f"no trading day at or before {value.date()}")
    return found[-1]


def build_walk_forward_plan(
    calendar: pd.DatetimeIndex,
    start_date: str,
    end_date: str,
    *,
    train_days: int = 1500,
    valid_days: int = 126,
    test_days: int = 63,
    label_buffer_days: int = 6,
    purge_days: int = 6,
    embargo_days: int = 6,
    min_rolling_oos_observations: int = 252,
    min_holdout_observations: int = 252,
) -> list[Fold]:
    dates = calendar[(calendar >= pd.Timestamp(start_date)) & (calendar <= pd.Timestamp(end_date))]
    positive = {
        "train_days": train_days,
        "valid_days": valid_days,
        "test_days": test_days,
        "min_rolling_oos_observations": min_rolling_oos_observations,
        "min_holdout_observations": min_holdout_observations,
    }
    invalid = [name for name, value in positive.items() if value <= 0]
    if invalid:
        raise ValueError(f"walk-forward day counts must be positive: {invalid}")
    if min_rolling_oos_observations % test_days:
        raise ValueError("min_rolling_oos_observations must be divisible by test_days")
    if min(purge_days, embargo_days, label_buffer_days) < 0:
        raise ValueError("walk-forward gaps and label buffer must be non-negative")
    holdout_span = min_holdout_observations + label_buffer_days
    required = (
        train_days + purge_days + valid_days + embargo_days + min_rolling_oos_observations + holdout_span + 1
    )
    if len(dates) < required:
        raise ValueError(
            f"walk-forward requires at least {required} shared trading days for the configured windows; "
            f"detected {len(dates)}"
        )
    holdout_start_pos = len(dates) - holdout_span - 1
    holdout_start = dates[holdout_start_pos]
    test_start_pos = train_days + purge_days + valid_days + embargo_days
    folds: list[Fold] = []
    index = 0
    while test_start_pos + test_days <= holdout_start_pos:
        test_start = dates[test_start_pos]
        test_end = dates[test_start_pos + test_days - 1]
        valid_end_pos = test_start_pos - embargo_days - 1
        valid_end = dates[valid_end_pos]
        valid_start_pos = valid_end_pos - valid_days + 1
        valid_start = dates[valid_start_pos]
        train_end_pos = valid_start_pos - purge_days - 1
        train_end = dates[train_end_pos]
        train_start = dates[train_end_pos - train_days + 1]
        folds.append(
            Fold(
                f"rolling_{index:02d}",
                (str(train_start.date()), str(train_end.date())),
                (str(valid_start.date()), str(valid_end.date())),
                (str(test_start.date()), str(test_end.date())),
            )
        )
        index += 1
        test_start_pos += test_days
    rolling_observations = sum(
        len(dates[(dates >= fold.test[0]) & (dates <= fold.test[1])]) for fold in folds
    )
    if rolling_observations < min_rolling_oos_observations:
        raise ValueError(
            f"walk-forward rolling OOS requires {min_rolling_oos_observations} observations; "
            f"planned {rolling_observations}"
        )
    valid_end_pos = holdout_start_pos - embargo_days - 1
    valid_end = dates[valid_end_pos]
    valid_start_pos = valid_end_pos - valid_days + 1
    valid_start = dates[valid_start_pos]
    train_end_pos = valid_start_pos - purge_days - 1
    train_end = dates[train_end_pos]
    train_start = dates[train_end_pos - train_days + 1]
    folds.append(
        Fold(
            "final_holdout",
            (str(train_start.date()), str(train_end.date())),
            (str(valid_start.date()), str(valid_end.date())),
            (str(holdout_start.date()), str(dates[-2].date())),
            True,
        )
    )
    return folds


def _evaluate_aggregate_oos_gate(
    settings: Settings,
    manifests: list[dict[str, Any]],
    prediction_path: Path,
    label_path: Path,
    portfolio_manifest: dict[str, Any],
    gate_path: Path,
) -> dict[str, object]:
    """Gate combined signals against one stateful rolling-OOS portfolio."""

    if not manifests:
        raise ValueError("aggregate OOS gate requires rolling component evidence")
    predictions = pd.read_parquet(prediction_path)
    if "score" in predictions:
        predictions = predictions[["score"]]
    labels = pd.read_parquet(label_path)
    combined_report = pd.read_parquet(_artifact_path(portfolio_manifest, "portfolio_report.parquet"))
    lineage_complete = all(bool(manifest.get("lineage", {}).get("complete")) for manifest in manifests)
    dirty_research_override = not lineage_complete and all(
        bool(manifest.get("lineage", {}).get("complete"))
        or bool(manifest.get("metrics", {}).get("dirty_research_override"))
        for manifest in manifests
    )
    prediction_paths = [_artifact_path(manifest, "oos_predictions.parquet") for manifest in manifests]
    unique_artifact = len({sha256_file(path) for path in prediction_paths}) == len(prediction_paths)
    metrics = derive_research_metrics(
        predictions,
        labels,
        combined_report,
        unique_artifact=unique_artifact,
        lineage_complete=lineage_complete,
        label_horizon_days=_research_label_horizon_days(settings),
    )
    research = settings.data.get("research", {})
    diagnostics_path = gate_path.with_suffix(".daily_ic.csv")
    daily_diagnostics = derive_daily_signal_diagnostics(predictions, labels)
    daily_diagnostics["rolling_12m_rank_ic"] = (
        daily_diagnostics["rank_ic"].rolling(252, min_periods=252).mean()
    )
    daily_diagnostics.reset_index().to_csv(diagnostics_path, index=False)
    thresholds = ResearchThresholds.from_mapping(
        research.get("promotion_thresholds", {}) if isinstance(research, dict) else {}
    )
    metrics["dirty_research_override"] = dirty_research_override
    report = evaluate_research_metrics(metrics, thresholds, allow_dirty_research=dirty_research_override)
    if dirty_research_override and report["passed"]:
        report["decision"] = "RESEARCH_ONLY"
    report["gate_mode"] = "aggregate_rolling_oos"
    report["component_count"] = len(manifests)
    report["portfolio_evidence"] = {
        "mode": "single_continuous_backtest",
        "externalRunId": str(portfolio_manifest.get("externalRunId", "")),
        "manifestPath": str(
            _artifact_path(portfolio_manifest, "portfolio_report.parquet").parent / "manifest.json"
        ),
        "predictionSha256": sha256_file(prediction_path),
        "reportSha256": sha256_file(_artifact_path(portfolio_manifest, "portfolio_report.parquet")),
    }
    fold_metric_keys = (
        "observations",
        "ic_mean",
        "rank_ic_mean",
        "icir",
        "rank_icir",
        "positive_ic_ratio",
        "positive_rank_ic_ratio",
    )
    fold_diagnostics: list[dict[str, Any]] = [
        {
            "runId": str(manifest.get("externalRunId", "")),
            "metrics": {key: manifest.get("metrics", {}).get(key) for key in fold_metric_keys},
        }
        for manifest in manifests
    ]
    fold_ics = [
        (str(item["runId"]), float(item["metrics"]["ic_mean"]))
        for item in fold_diagnostics
        if isinstance(item["metrics"].get("ic_mean"), (int, float))
        and math.isfinite(float(item["metrics"]["ic_mean"]))
    ]
    fold_rank_ics = [
        float(item["metrics"]["rank_ic_mean"])
        for item in fold_diagnostics
        if isinstance(item["metrics"].get("rank_ic_mean"), (int, float))
        and math.isfinite(float(item["metrics"]["rank_ic_mean"]))
    ]
    annual = daily_diagnostics.groupby(daily_diagnostics.index.year)[["ic", "rank_ic"]].mean()
    rolling_rank = daily_diagnostics["rolling_12m_rank_ic"].dropna()
    report["signal_diagnostics"] = {
        "dailyArtifactPath": str(diagnostics_path),
        "dailyObservationCount": len(daily_diagnostics),
        "folds": fold_diagnostics,
        "foldStability": {
            "icByFold": [value for _, value in fold_ics],
            "rankIcByFold": fold_rank_ics,
            "positiveIcFoldRatio": (
                sum(value > 0 for _, value in fold_ics) / len(fold_ics) if fold_ics else 0.0
            ),
            "bestFold": (
                {
                    "runId": max(fold_ics, key=lambda item: item[1])[0],
                    "ic": max(value for _, value in fold_ics),
                }
                if fold_ics
                else None
            ),
            "worstFold": (
                {
                    "runId": min(fold_ics, key=lambda item: item[1])[0],
                    "ic": min(value for _, value in fold_ics),
                }
                if fold_ics
                else None
            ),
            "icDispersion": float(pd.Series([value for _, value in fold_ics]).std(ddof=1))
            if len(fold_ics) > 1
            else 0.0,
        },
        "annualIc": [
            {"year": int(year), "ic": float(row.ic), "rankIc": float(row.rank_ic)}
            for year, row in annual.iterrows()
        ],
        "rolling12mRankIc": {
            "windowSessions": 252,
            "latest": float(rolling_rank.iloc[-1]) if len(rolling_rank) else None,
            "minimum": float(rolling_rank.min()) if len(rolling_rank) else None,
            "maximum": float(rolling_rank.max()) if len(rolling_rank) else None,
        },
    }
    write_gate_report(report, gate_path)
    return report


def run_walk_forward(
    settings: Settings,
    *,
    start_date: str,
    end_date: str,
    benchmark: str = "SH000300",
    topn: int | None = None,
    model_profile: str | Path | None = None,
    acceptance_mode: bool = False,
    interrupt_after_fold: int | None = None,
    checkpoint_namespace: str = "default",
) -> Path:
    if interrupt_after_fold is not None and (not acceptance_mode or interrupt_after_fold < 1):
        raise ValueError("interrupt_after_fold requires Full Walk-forward Acceptance and must be positive")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", checkpoint_namespace):
        raise ValueError("checkpoint_namespace must be a safe 1-64 character identifier")
    if acceptance_mode:
        revision = git_revision(Path(__file__).resolve().parents[2])
        if not revision.get("commit") or revision.get("dirty") is not False:
            raise RuntimeError("Full Walk-forward Acceptance requires a clean committed code revision")
    settings, pinned_dataset = pin_dataset(settings)
    orchestration_started = time.perf_counter()
    runtime = resolve_runtime(load_model_profile(settings, model_profile))
    calendar = shared_research_calendar(settings)
    research = settings.data.get("research", {})
    thresholds = ResearchThresholds.from_mapping(
        research.get("promotion_thresholds", {}) if isinstance(research, dict) else {}
    )
    timing = label_timing_from_settings(settings)
    walk_cfg = research.get("walk_forward", {}) if isinstance(research, dict) else {}
    walk_cfg = walk_cfg if isinstance(walk_cfg, dict) else {}
    requested_purge, purge_days = effective_label_gap(walk_cfg.get("purge_days"), timing)
    requested_embargo, embargo_days = effective_label_gap(walk_cfg.get("embargo_days"), timing)
    legacy_buffer = research.get("walk_forward_label_buffer_days") if isinstance(research, dict) else None
    requested_buffer, label_buffer_days = effective_label_gap(
        walk_cfg.get("label_buffer_days", legacy_buffer), timing
    )
    folds = build_walk_forward_plan(
        calendar,
        start_date,
        end_date,
        train_days=int(walk_cfg.get("train_days", 1500)),
        valid_days=int(walk_cfg.get("valid_days", 126)),
        test_days=int(walk_cfg.get("test_days", 63)),
        label_buffer_days=label_buffer_days,
        purge_days=purge_days,
        embargo_days=embargo_days,
        min_rolling_oos_observations=int(
            walk_cfg.get("min_rolling_oos_observations", thresholds.min_observations)
        ),
        min_holdout_observations=thresholds.min_observations,
    )
    fold_integrity = validate_fold_integrity(
        folds,
        calendar,
        label_lookahead_sessions=timing.lookahead_days,
    )
    timing_contract = {
        **timing.to_manifest(),
        "requestedPurgeDays": requested_purge,
        "effectivePurgeDays": purge_days,
        "requestedEmbargoDays": requested_embargo,
        "effectiveEmbargoDays": embargo_days,
        "requestedLabelBufferDays": requested_buffer,
        "effectiveLabelBufferDays": label_buffer_days,
    }
    prepared_feature_data: pd.DataFrame | None = None
    feature_store_metadata: dict[str, object] | None = None
    feature_store_seconds = 0.0
    if feature_store_enabled(settings):
        feature_store_started = time.perf_counter()
        prepared_feature_data, feature_store_metadata = prepare_feature_data(
            settings, folds[0].train[0], folds[-1].test[1]
        )
        feature_store_seconds = time.perf_counter() - feature_store_started
    if acceptance_mode and (
        not feature_store_metadata
        or feature_store_metadata.get("cacheStatus") != "REUSED"
        or feature_store_metadata.get("rawMaterializationCalls") != 0
    ):
        raise RuntimeError(
            "Full Walk-forward Acceptance requires one pre-existing immutable FeatureSnapshot "
            "with rawMaterializationCalls=0"
        )
    run_root = settings.paths.output / "research" / "walk_forward" / checkpoint_namespace
    run_root.mkdir(parents=True, exist_ok=True)
    manifests: list[dict[str, Any]] = []
    component_runs: list[dict[str, Any]] = []

    def execute_fold(
        fold: Fold,
    ) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
        checkpoint_fingerprint = _checkpoint_fingerprint(
            settings,
            fold,
            runtime_fingerprint=runtime.fingerprint,
            benchmark=benchmark,
            topn=topn,
        )
        checkpoint = run_root / f"{fold.key}_{checkpoint_fingerprint}.json"
        checkpoint_validation = _inspect_checkpoint(settings, checkpoint, checkpoint_fingerprint)
        fold_manifest_path = checkpoint_validation.manifest_path
        reused = fold_manifest_path is not None
        if fold_manifest_path is None:
            result_path = train_backtest_select(
                settings,
                train=fold.train,
                valid=fold.valid,
                test=fold.test,
                benchmark=benchmark,
                topn=topn,
                experiment_name=f"lean_csi300_{runtime.profile.name}_{fold.key}",
                run_kind="final_holdout" if fold.final_holdout else "walk_forward_fold",
                runtime=runtime,
                promotion_mode="holdout" if fold.final_holdout else "component",
                prepared_feature_data=prepared_feature_data,
                feature_store_metadata=feature_store_metadata,
                artifact_level="full" if fold.final_holdout else "minimal",
            )
            fold_manifest_path = result_path
            checkpoint_tmp = checkpoint.with_suffix(".json.tmp")
            checkpoint_payload = _checkpoint_payload(fold_manifest_path, checkpoint_fingerprint)
            checkpoint_payload["runtimeFingerprint"] = runtime.fingerprint
            checkpoint_tmp.write_text(
                json.dumps(checkpoint_payload, indent=2),
                encoding="utf-8",
            )
            os.replace(checkpoint_tmp, checkpoint)
        manifest = json.loads(fold_manifest_path.read_text(encoding="utf-8"))
        promotion = manifest.get("promotion", {})
        if not isinstance(promotion, dict):
            raise ValueError(f"fold manifest has no promotion contract: {fold.key}")
        expected_statuses = {"CANDIDATE", "REJECTED"} if fold.final_holdout else {"CANDIDATE"}
        expected_mode = "final_holdout" if fold.final_holdout else "component_validation"
        if promotion.get("status") not in expected_statuses or promotion.get("gateMode") != expected_mode:
            raise ValueError(
                f"fold {fold.key} has incompatible promotion contract: "
                f"status={promotion.get('status')}, gateMode={promotion.get('gateMode')}"
            )
        if fold.final_holdout:
            report = pd.read_parquet(_artifact_path(manifest, "portfolio_report.parquet"))
            audit = pd.read_parquet(_artifact_path(manifest, "strategy_audit.parquet"))
            holding = pd.read_parquet(_artifact_path(manifest, "holdings.parquet"))
        else:
            report = pd.DataFrame()
            audit = pd.DataFrame()
            holding = pd.DataFrame()
        component_run = {
            "key": fold.key,
            "externalRunId": str(manifest["externalRunId"]),
            "manifestPath": str(fold_manifest_path),
            "checkpointReused": reused,
            "checkpointStatus": (
                "REUSED"
                if reused
                else "INVALIDATED_REBUILT"
                if checkpoint_validation.status in {"CORRUPTED", "STALE"}
                else "BUILT"
            ),
            "checkpointInvalidationReason": checkpoint_validation.reason,
            "promotionMode": "final_holdout" if fold.final_holdout else "component_validation",
            "artifactMode": "portfolio_full" if fold.final_holdout else "signal_only",
            "portfolioBacktestExecuted": fold.final_holdout,
            "timings": manifest.get("timings", {}),
        }
        return manifest, report, audit, holding, component_run

    rolling_folds = [fold for fold in folds if not fold.final_holdout]
    final_folds = [fold for fold in folds if fold.final_holdout]
    if len(final_folds) != 1:
        raise ValueError("walk-forward plan must contain exactly one final holdout")
    for fold in rolling_folds:
        manifest, _, _, _, component_run = execute_fold(fold)
        manifests.append(manifest)
        component_runs.append(component_run)
        if interrupt_after_fold is not None and len(manifests) == interrupt_after_fold:
            raise RuntimeError(f"acceptance fault injection interrupted after fold {fold.key}")

    rolling_ids = [str(manifest["externalRunId"]) for manifest in manifests]
    aggregate_key = hashlib.sha256("|".join(rolling_ids).encode()).hexdigest()[:32]
    aggregate_dir = run_root / f"aggregate_oos_{aggregate_key}"
    expected_oos_dates = pd.DatetimeIndex([])
    for fold in rolling_folds:
        expected_oos_dates = expected_oos_dates.append(
            calendar[(calendar >= fold.test[0]) & (calendar <= fold.test[1])]
        )
    prediction_path, label_path, oos_stream = _write_continuous_oos_stream(
        manifests,
        aggregate_dir,
        expected_dates=expected_oos_dates,
    )
    portfolio_manifest_path = backtest_predictions(
        settings,
        prediction_path,
        benchmark=benchmark,
        topn=topn,
        artifact_level="minimal",
    )
    portfolio_manifest = json.loads(portfolio_manifest_path.read_text(encoding="utf-8"))
    continuous_report = pd.read_parquet(_artifact_path(portfolio_manifest, "portfolio_report.parquet"))
    continuous_audit = pd.read_parquet(_artifact_path(portfolio_manifest, "strategy_audit.parquet"))
    continuous_holdings = pd.read_parquet(_artifact_path(portfolio_manifest, "holdings.parquet"))
    continuity = _verify_fold_boundary_continuity(
        manifests,
        continuous_holdings,
        continuous_audit,
        continuous_report,
        initial_cash=float(research.get("backtest_account", 500_000)),
    )
    continuity_path = aggregate_dir / "fold_boundary_continuity.json"
    continuity_path.write_text(json.dumps(continuity, ensure_ascii=False, indent=2), encoding="utf-8")
    aggregate_gate_path = run_root / f"aggregate_oos_gate_{aggregate_key}.json"
    aggregate_gate = _evaluate_aggregate_oos_gate(
        settings,
        manifests,
        prediction_path,
        label_path,
        portfolio_manifest,
        aggregate_gate_path,
    )
    selection_lock_path = aggregate_dir / "research_selection_lock.json"
    selection_lock = _write_research_selection_lock(
        selection_lock_path,
        fold_plan=folds,
        rolling_manifests=manifests,
        aggregate_prediction_sha256=str(oos_stream["aggregatePredictionSha256"]),
        thresholds=thresholds,
    )

    final_fold = final_folds[0]
    final_manifest, final_report, _, _, final_run = execute_fold(final_fold)
    final_run["selectionLockSha256"] = selection_lock["lockSha256"]
    final_run["accessedAfterSelectionLock"] = True
    if pd.Timestamp(final_report.index.min()) <= pd.Timestamp(continuous_report.index.max()):
        raise ValueError(f"overlapping OOS reports at fold {final_fold.key}")
    manifests.append(final_manifest)
    component_runs.append(final_run)
    processor_isolation = validate_processor_isolation(manifests)

    # The top-level portfolio artifacts are the rolling OOS evidence used by the
    # aggregate gate.  The untouched final holdout remains an independent run.
    combined = continuous_report
    combined_audit = continuous_audit
    combined_holdings = continuous_holdings
    fold_ids = [item["externalRunId"] for item in manifests]
    external_id = hashlib.sha256("|".join(fold_ids).encode()).hexdigest()[:32]
    output_dir = settings.paths.output / "research" / external_id
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_selection_lock_path = output_dir / "research_selection_lock.json"
    shutil.copy2(selection_lock_path, evidence_selection_lock_path)
    evidence_prediction_path = output_dir / "oos_predictions.parquet"
    evidence_label_path = output_dir / "oos_labels.parquet"
    shutil.copy2(prediction_path, evidence_prediction_path)
    shutil.copy2(label_path, evidence_label_path)
    aggregate_snapshot_path = prediction_snapshot_path(prediction_path)
    evidence_snapshot_path = prediction_snapshot_path(evidence_prediction_path)
    if aggregate_snapshot_path.is_file():
        shutil.copy2(aggregate_snapshot_path, evidence_snapshot_path)
    final_holdout_artifacts = {
        "final_holdout_predictions.parquet": _artifact_path(final_manifest, "oos_predictions.parquet"),
        "final_holdout_labels.parquet": _artifact_path(final_manifest, "oos_labels.parquet"),
        "final_holdout_portfolio_report.parquet": _artifact_path(final_manifest, "portfolio_report.parquet"),
        "final_holdout_holdings.parquet": _artifact_path(final_manifest, "holdings.parquet"),
    }
    for name, source in final_holdout_artifacts.items():
        shutil.copy2(source, output_dir / name)
    report_path = output_dir / "portfolio_report.parquet"
    audit_path = output_dir / "strategy_audit.parquet"
    holdings_path = output_dir / "holdings.parquet"
    timings_path = output_dir / "timings.json"
    combined.to_parquet(report_path)
    combined_audit.to_parquet(audit_path, index=False)
    combined_holdings.to_parquet(holdings_path, index=False)
    metrics: dict[str, float] = {}
    for column in ("return", "bench", "cost"):
        if column in combined:
            values = pd.to_numeric(combined[column], errors="coerce").dropna()
            metrics[f"{column}Total"] = float((1.0 + values).prod() - 1.0)
    component_lineage = [manifest.get("lineage", {}) for manifest in manifests]
    component_lineage_ids = [
        str(item.get("lineageId")) for item in component_lineage if isinstance(item, dict)
    ]
    aggregate_lineage = {
        "componentLineageIds": component_lineage_ids,
        "complete": len(component_lineage_ids) == len(manifests)
        and all(bool(item.get("complete")) for item in component_lineage if isinstance(item, dict)),
        "qlibPlatformCommit": component_lineage[-1].get("qlibPlatformCommit"),
        "qlibCommit": component_lineage[-1].get("qlibCommit"),
        "datasetFingerprint": manifests[-1]["dataset"].get("fingerprint"),
        "rollingOosPredictionsSha256": sha256_file(prediction_path),
        "rollingOosPortfolioReportSha256": sha256_file(
            _artifact_path(portfolio_manifest, "portfolio_report.parquet")
        ),
    }
    aggregate_lineage["lineageId"] = hashlib.sha256(
        json.dumps(aggregate_lineage, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:32]
    final_gate_path = Path(str(final_manifest.get("promotion", {}).get("gateReportPath") or ""))
    if not final_gate_path.is_file():
        raise ValueError("final holdout gate report is missing")
    final_gate_report = json.loads(final_gate_path.read_text(encoding="utf-8"))
    research_quality_passed = bool(aggregate_gate.get("passed")) and bool(final_gate_report.get("passed"))
    promoted = (
        bool(aggregate_lineage["complete"])
        and research_quality_passed
        and all(
            manifest.get("promotion", {}).get("status") in {"CANDIDATE", "PROMOTED"}
            for manifest in manifests[:-1]
        )
    )
    aggregate_phases = _aggregate_component_timings(manifests)
    portfolio_phases = portfolio_manifest.get("timings", {}).get("phasesSeconds", {})
    if isinstance(portfolio_phases, dict):
        for key, value in portfolio_phases.items():
            aggregate_phases[f"continuous_oos_{key}"] = round(float(value), 6)
    aggregate_phases["shared_feature_store_seconds"] = round(feature_store_seconds, 6)
    timings = {
        "clock": "time.perf_counter",
        "phasesSeconds": aggregate_phases,
        "totalSeconds": round(sum(aggregate_phases.values()), 6),
        "orchestrationWallSeconds": round(time.perf_counter() - orchestration_started, 6),
        "checkpointReuseCount": sum(bool(item["checkpointReused"]) for item in component_runs),
        "reportRenderingIncluded": False,
    }
    performance = performance_baseline(
        component_runs,
        feature_store=feature_store_metadata,
        feature_seconds=feature_store_seconds,
        portfolio_manifest=portfolio_manifest,
        orchestration_seconds=time.perf_counter() - orchestration_started,
    )
    write_timings(timings_path, runtime, timings)
    evidence_path = output_dir / "walk_forward_evidence.json"
    checkpoint_statuses = [str(item["checkpointStatus"]) for item in component_runs]
    evidence = {
        "acceptanceType": "FULL_WALK_FORWARD_V1_MODEL_EVIDENCE",
        "checkpointNamespace": checkpoint_namespace,
        "systemAcceptance": "PASS",
        "walkForwardIntegrity": "PASS",
        "researchQuality": (
            "PASS"
            if research_quality_passed
            else "REVIEW"
            if "RESEARCH_REVIEW"
            in {str(aggregate_gate.get("decision")), str(final_gate_report.get("decision"))}
            else "REJECT"
        ),
        "performanceAcceptance": "BASELINE_RECORDED",
        "data": manifests[0].get("dataset"),
        "featureSnapshot": {
            **(feature_store_metadata or {}),
            "sameAcrossFolds": True,
        },
        "foldIntegrity": {
            **fold_integrity,
            "duplicatePredictionRows": oos_stream["duplicateRows"],
            "missingExpectedFoldDates": oos_stream["missingExpectedFoldDates"],
            "outOfOrderRows": oos_stream["outOfOrderRows"],
        },
        "processorIsolation": processor_isolation,
        "oosPrediction": oos_stream,
        "stateContinuity": continuity,
        "checkpointRecovery": {
            "statuses": checkpoint_statuses,
            "validFoldReuseCount": checkpoint_statuses.count("REUSED"),
            "invalidatedAndRebuiltCount": checkpoint_statuses.count("INVALIDATED_REBUILT"),
            "allPayloadsValidatedBeforeReuse": True,
        },
        "finalHoldout": {
            "isolated": True,
            "usedForResearchSelection": False,
            "accessedBeforeFinalization": False,
            "rollingOosOverlapDates": 0,
            "selectionLockPath": str(evidence_selection_lock_path),
            "selectionLockSha256": selection_lock["lockSha256"],
            "gate": final_gate_report,
            "predictionSha256": sha256_file(output_dir / "final_holdout_predictions.parquet"),
            "portfolioSha256": sha256_file(output_dir / "final_holdout_portfolio_report.parquet"),
            "holdingsSha256": sha256_file(output_dir / "final_holdout_holdings.parquet"),
        },
        "researchSelectionLock": selection_lock,
        "model": {
            "profile": runtime.profile.name,
            "family": runtime.profile.family,
            "aggregatePredictionSha256": oos_stream["aggregatePredictionSha256"],
        },
        "researchStability": aggregate_gate.get("signal_diagnostics"),
        "performance": performance,
    }
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    payload = {
        "schemaVersion": "2.0",
        "externalRunId": external_id,
        "runKind": "walk_forward",
        "checkpointNamespace": checkpoint_namespace,
        "name": f"Qlib CSI300 walk-forward {start_date}..{end_date}",
        "dataset": manifests[-1]["dataset"],
        "datasetVersionId": pinned_dataset.version_id,
        "model": {
            "name": f"{runtime.profile.name}-WalkForward",
            "fingerprint": external_id,
            "artifactRole": "RESEARCH_EVIDENCE",
            "deployable": False,
        },
        "runtime": runtime.to_manifest(),
        "canonicalConfig": manifests[-1].get("canonicalConfig"),
        "portfolioPolicySha256": manifests[-1].get("portfolioPolicySha256"),
        "lineage": aggregate_lineage,
        "promotion": {
            "status": "PROMOTED" if promoted else "CANDIDATE",
            "decision": (
                "PROMOTE"
                if promoted
                else str(aggregate_gate.get("decision"))
                if not aggregate_gate.get("passed")
                else str(final_gate_report.get("decision"))
            ),
            "gateMode": "aggregate_oos_and_final_holdout",
            "aggregateOosGate": aggregate_gate,
            "finalHoldoutGate": final_gate_report,
            "finalHoldoutRunPromotion": final_manifest.get("promotion", {}),
            "componentValidationReports": [manifest.get("promotion", {}) for manifest in manifests[:-1]],
        },
        "timings": timings,
        "featureStore": feature_store_metadata,
        "walkForwardEvidence": evidence,
        "folds": [asdict(fold) for fold in folds],
        "labelTiming": timing_contract,
        "execution": manifests[-1]["execution"],
        "metrics": metrics,
        "evaluationScopes": {
            "rollingOosPortfolio": "single_continuous_account",
            "topLevelPortfolioArtifacts": "rolling_oos_only",
            "finalHoldout": "independent_untouched_component_run",
            "researchSelectionLock": str(evidence_selection_lock_path),
        },
        "oosStream": oos_stream,
        "aggregatePortfolioRun": {
            "externalRunId": str(portfolio_manifest.get("externalRunId", "")),
            "manifestPath": str(portfolio_manifest_path),
            "stateMode": "single_continuous_account",
            "foldBoundaryContinuityPath": str(continuity_path),
        },
        "componentRuns": component_runs,
        "researchRelease": {
            "status": "APPROVED_RECIPE" if promoted else "CANDIDATE",
            "evidenceRunId": external_id,
            "requiresProductionRefit": True,
            "deployableModelArtifact": None,
        },
        "artifacts": [
            {"name": report_path.name, "localPath": str(report_path), "rows": len(combined)},
            {"name": audit_path.name, "localPath": str(audit_path), "rows": len(combined_audit)},
            {"name": holdings_path.name, "localPath": str(holdings_path), "rows": len(combined_holdings)},
            {"name": timings_path.name, "localPath": str(timings_path)},
            {
                "name": evidence_prediction_path.name,
                "localPath": str(evidence_prediction_path),
                "rows": oos_stream["predictionRows"],
            },
            {
                "name": evidence_label_path.name,
                "localPath": str(evidence_label_path),
                "rows": oos_stream["labelRows"],
            },
            *(
                [{"name": evidence_snapshot_path.name, "localPath": str(evidence_snapshot_path)}]
                if evidence_snapshot_path.is_file()
                else []
            ),
            {"name": continuity_path.name, "localPath": str(continuity_path)},
            {"name": aggregate_gate_path.name, "localPath": str(aggregate_gate_path)},
            {
                "name": evidence_selection_lock_path.name,
                "localPath": str(evidence_selection_lock_path),
            },
            {"name": evidence_path.name, "localPath": str(evidence_path)},
            *[{"name": name, "localPath": str(output_dir / name)} for name in final_holdout_artifacts],
        ],
    }
    from .backtest_report import ReportArtifacts, write_backtest_report

    payload["artifacts"].extend(
        ReportArtifacts(
            markdown_path=output_dir / "backtest_report.md",
            pdf_path=output_dir / "backtest_report.pdf",
            assets_dir=output_dir / "report_assets",
        ).manifest_entries()
    )
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    from .dataset_registry import DatasetRegistry

    DatasetRegistry(settings.registry_path).register_research_manifest(manifest_path)
    write_backtest_report(settings, output_dir)
    return manifest_path
