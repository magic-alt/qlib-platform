from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .settings import Settings
from .model_runtime import load_model_profile, resolve_runtime, write_timings
from .research_gate import (
    ResearchPromotionError,
    derive_daily_signal_diagnostics,
    ResearchThresholds,
    derive_research_metrics,
    evaluate_research_metrics,
    write_gate_report,
)
from .store import sha256_file
from .lineage import dirty_research_override_enabled, git_revision, resolve_qlib_repo
from .universe import membership_fingerprint
from .research_timing import effective_label_gap, label_timing_from_settings, shared_research_calendar
from .train_select import _research_label_horizon_days, train_backtest_select
from .canonical_config import StrategySpec
from .feature_store import feature_store_enabled, prepare_feature_data
from .dataset_resolver import pin_dataset
from .prediction_backtest import backtest_predictions


@dataclass(frozen=True)
class Fold:
    key: str
    train: tuple[str, str]
    valid: tuple[str, str]
    test: tuple[str, str]
    final_holdout: bool = False


def _artifact_path(manifest: dict[str, Any], name: str) -> Path:
    for item in manifest.get("artifacts", []):
        if isinstance(item, dict) and item.get("name") == name and item.get("localPath"):
            return Path(str(item["localPath"]))
    raise FileNotFoundError(f"manifest artifact is missing: {name}")


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
    return frame[[column]].sort_index()


def _write_continuous_oos_stream(
    manifests: list[dict[str, Any]], output_dir: Path
) -> tuple[Path, Path, dict[str, object]]:
    """Persist one strictly ordered prediction/label stream from rolling folds."""

    if not manifests:
        raise ValueError("continuous OOS stream requires rolling component manifests")
    output_dir.mkdir(parents=True, exist_ok=True)
    predictions: list[pd.DataFrame] = []
    labels: list[pd.DataFrame] = []
    components: list[dict[str, object]] = []
    previous_end: pd.Timestamp | None = None
    for manifest in manifests:
        pred = _read_oos_frame(manifest, "oos_predictions.parquet", "score")
        label = _read_oos_frame(manifest, "oos_labels.parquet", "label")
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
    prediction_path = output_dir / "oos_predictions.parquet"
    label_path = output_dir / "oos_labels.parquet"
    combined_predictions.to_parquet(prediction_path)
    combined_labels.to_parquet(label_path)
    metadata: dict[str, object] = {
        "componentCount": len(components),
        "components": components,
        "startDate": components[0]["startDate"],
        "endDate": components[-1]["endDate"],
        "predictionRows": len(combined_predictions),
        "labelRows": len(combined_labels),
        "predictionDates": int(combined_predictions.index.get_level_values("datetime").nunique()),
        "duplicateRows": 0,
    }
    return prediction_path, label_path, metadata


def _verify_fold_boundary_continuity(
    manifests: list[dict[str, Any]], holdings: pd.DataFrame, audit: pd.DataFrame
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
        if prior_dates.empty or next_dates.empty:
            results.append({**base, "status": "NO_COMPARABLE_HOLDING_SNAPSHOTS", "continuingPositions": 0})
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
            }
        )
    if unexpected_resets:
        raise RuntimeError(
            f"fold boundary holding_days reset in continuous backtest: {unexpected_resets[:5]}"
        )
    return {
        "passed": True,
        "portfolioState": "SINGLE_CONTINUOUS_ACCOUNT",
        "boundaryCount": len(results),
        "boundaries": results,
        "unexpectedHoldingDayResetCount": 0,
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
        "promotionContract": "release-v3" if fold.final_holdout else "component-validation-v3",
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[
        :16
    ]


def _validated_checkpoint_manifest(
    settings: Settings, checkpoint: Path, expected_fingerprint: str
) -> Path | None:
    if not checkpoint.is_file():
        return None
    try:
        payload = json.loads(checkpoint.read_text(encoding="utf-8"))
        if payload.get("checkpointFingerprint") != expected_fingerprint:
            return None
        manifest_path = Path(str(payload["manifest"]))
        if not manifest_path.is_file():
            return None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    dataset_manifest = settings.qlib_data_uri / "dataset_manifest.json"
    if dataset_manifest.is_file():
        current_dataset = json.loads(dataset_manifest.read_text(encoding="utf-8"))
        current_fingerprint = str(
            current_dataset.get("sha256", current_dataset.get("dataset_id", "unversioned"))
        )
        if str(manifest.get("dataset", {}).get("fingerprint")) != current_fingerprint:
            return None
    lineage = manifest.get("lineage", {})
    if not isinstance(lineage, dict) or not lineage.get("lineageId"):
        return None
    if not lineage.get("complete") and not dirty_research_override_enabled(settings, lineage):
        return None
    artifacts = manifest.get("artifacts", [])
    if not isinstance(artifacts, list) or not artifacts:
        return None
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not Path(str(artifact.get("localPath", ""))).is_file():
            return None
    return manifest_path


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
    report["signal_diagnostics"] = {
        "dailyArtifactPath": str(diagnostics_path),
        "dailyObservationCount": len(daily_diagnostics),
        "folds": [
            {
                "runId": str(manifest.get("externalRunId", "")),
                "metrics": {key: manifest.get("metrics", {}).get(key) for key in fold_metric_keys},
            }
            for manifest in manifests
        ],
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
) -> Path:
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
    run_root = settings.paths.output / "research" / "walk_forward"
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
        fold_manifest_path = _validated_checkpoint_manifest(settings, checkpoint, checkpoint_fingerprint)
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
                promotion_mode="release" if fold.final_holdout else "component",
                prepared_feature_data=prepared_feature_data,
                feature_store_metadata=feature_store_metadata,
                artifact_level="full" if fold.final_holdout else "minimal",
            )
            if fold.final_holdout and result_path.name != "manifest.json":
                model_id = str(pd.read_csv(result_path)["model_id"].iloc[0])
                fold_manifest_path = settings.paths.output / "research" / model_id / "manifest.json"
            else:
                fold_manifest_path = result_path
            checkpoint_tmp = checkpoint.with_suffix(".json.tmp")
            checkpoint_tmp.write_text(
                json.dumps(
                    {
                        "manifest": str(fold_manifest_path),
                        "runtimeFingerprint": runtime.fingerprint,
                        "checkpointFingerprint": checkpoint_fingerprint,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            os.replace(checkpoint_tmp, checkpoint)
        manifest = json.loads(fold_manifest_path.read_text(encoding="utf-8"))
        promotion = manifest.get("promotion", {})
        if not isinstance(promotion, dict):
            raise ValueError(f"fold manifest has no promotion contract: {fold.key}")
        expected_status = "PROMOTED" if fold.final_holdout else "CANDIDATE"
        expected_mode = "release" if fold.final_holdout else "component_validation"
        dirty_candidate = (
            fold.final_holdout
            and promotion.get("status") == "CANDIDATE"
            and bool(manifest.get("metrics", {}).get("dirty_research_override"))
        )
        if (promotion.get("status") != expected_status and not dirty_candidate) or promotion.get(
            "gateMode"
        ) != expected_mode:
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
            "promotionMode": "release" if fold.final_holdout else "component_validation",
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

    rolling_ids = [str(manifest["externalRunId"]) for manifest in manifests]
    aggregate_key = hashlib.sha256("|".join(rolling_ids).encode()).hexdigest()[:32]
    aggregate_dir = run_root / f"aggregate_oos_{aggregate_key}"
    prediction_path, label_path, oos_stream = _write_continuous_oos_stream(manifests, aggregate_dir)
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
    continuity = _verify_fold_boundary_continuity(manifests, continuous_holdings, continuous_audit)
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
    if not aggregate_gate["passed"] and aggregate_gate["decision"] == "REJECT":
        rejection_manifest = run_root / f"aggregate_oos_{aggregate_key}.manifest.json"
        rejection_manifest.write_text(
            json.dumps(
                {
                    "schemaVersion": "2.0",
                    "externalRunId": aggregate_key,
                    "runKind": "walk_forward_aggregate_oos",
                    "promotion": {
                        "status": "REJECTED",
                        "decision": "REJECT",
                        "gateMode": "aggregate_rolling_oos",
                        "gateReportPath": str(aggregate_gate_path),
                    },
                    "oosStream": oos_stream,
                    "aggregatePortfolioRun": {
                        "externalRunId": str(portfolio_manifest.get("externalRunId", "")),
                        "manifestPath": str(portfolio_manifest_path),
                        "stateMode": "single_continuous_account",
                        "foldBoundaryContinuityPath": str(continuity_path),
                    },
                    "componentRuns": component_runs,
                    "artifacts": [
                        {"name": prediction_path.name, "localPath": str(prediction_path)},
                        {"name": label_path.name, "localPath": str(label_path)},
                        {"name": continuity_path.name, "localPath": str(continuity_path)},
                        {"name": aggregate_gate_path.name, "localPath": str(aggregate_gate_path)},
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        raise ResearchPromotionError(rejection_manifest)

    final_fold = final_folds[0]
    final_manifest, final_report, _, _, final_run = execute_fold(final_fold)
    if pd.Timestamp(final_report.index.min()) <= pd.Timestamp(continuous_report.index.max()):
        raise ValueError(f"overlapping OOS reports at fold {final_fold.key}")
    manifests.append(final_manifest)
    component_runs.append(final_run)

    # The top-level portfolio artifacts are the rolling OOS evidence used by the
    # aggregate gate.  The untouched final holdout remains an independent run.
    combined = continuous_report
    combined_audit = continuous_audit
    combined_holdings = continuous_holdings
    fold_ids = [item["externalRunId"] for item in manifests]
    external_id = hashlib.sha256("|".join(fold_ids).encode()).hexdigest()[:32]
    output_dir = settings.paths.output / "research" / external_id
    output_dir.mkdir(parents=True, exist_ok=True)
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
    promoted = (
        bool(aggregate_lineage["complete"])
        and all(
            manifest.get("promotion", {}).get("status") in {"CANDIDATE", "PROMOTED"}
            for manifest in manifests[:-1]
        )
        and final_manifest.get("promotion", {}).get("status") == "PROMOTED"
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
    write_timings(timings_path, runtime, timings)
    payload = {
        "schemaVersion": "2.0",
        "externalRunId": external_id,
        "runKind": "walk_forward",
        "name": f"Qlib CSI300 walk-forward {start_date}..{end_date}",
        "dataset": manifests[-1]["dataset"],
        "datasetVersionId": pinned_dataset.version_id,
        "model": {
            "name": (
                "Alpha158-LGBM-WalkForward"
                if runtime.profile.family == "lightgbm"
                else "Alpha158-DNN-WalkForward"
            ),
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
            "decision": "PROMOTE" if promoted else "RESEARCH_ONLY",
            "gateMode": "aggregate_oos_and_final_holdout",
            "aggregateOosGate": aggregate_gate,
            "finalHoldoutGate": final_manifest.get("promotion", {}),
            "componentValidationReports": [manifest.get("promotion", {}) for manifest in manifests[:-1]],
        },
        "timings": timings,
        "featureStore": feature_store_metadata,
        "folds": [asdict(fold) for fold in folds],
        "labelTiming": timing_contract,
        "execution": manifests[-1]["execution"],
        "metrics": metrics,
        "evaluationScopes": {
            "rollingOosPortfolio": "single_continuous_account",
            "topLevelPortfolioArtifacts": "rolling_oos_only",
            "finalHoldout": "independent_untouched_component_run",
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
                "name": prediction_path.name,
                "localPath": str(prediction_path),
                "rows": oos_stream["predictionRows"],
            },
            {"name": label_path.name, "localPath": str(label_path), "rows": oos_stream["labelRows"]},
            {"name": continuity_path.name, "localPath": str(continuity_path)},
            {"name": aggregate_gate_path.name, "localPath": str(aggregate_gate_path)},
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
