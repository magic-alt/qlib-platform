from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .monitoring import evaluate_signal_drift
from .settings import Settings


@dataclass(frozen=True)
class SignalHealthReport:
    passed: bool
    decision: str
    reasons: list[str]
    metrics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _dataset_snapshot(settings: Settings) -> tuple[dict[str, Any], str]:
    from .store import sha256_file

    path = settings.qlib_data_uri / "dataset_manifest.json"
    if not path.is_file():
        raise FileNotFoundError(f"Qlib dataset manifest is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8")), sha256_file(path)


def _expected_next_open_date(settings: Settings, signal_date: str) -> str | None:
    path = settings.paths.metadata / "trade_calendar.parquet"
    if not path.is_file():
        return None
    calendar = pd.read_parquet(path)
    if not {"cal_date", "is_open"}.issubset(calendar.columns):
        return None
    open_dates = (
        pd.DatetimeIndex(
            pd.to_datetime(
                calendar.loc[pd.to_numeric(calendar["is_open"], errors="coerce") == 1, "cal_date"],
                errors="coerce",
            ).dropna()
        )
        .normalize()
        .sort_values()
    )
    signal = pd.Timestamp(signal_date).normalize()
    if signal not in open_dates:
        return None
    future = open_dates[open_dates > signal]
    return future[0].strftime("%Y-%m-%d") if len(future) else None


def _score_psi(values: pd.Series, quantiles: object) -> float | None:
    if not isinstance(quantiles, list) or len(quantiles) < 3:
        return None
    edges = np.unique(np.asarray(quantiles, dtype=float))
    if len(edges) < 3 or not np.isfinite(edges).all():
        return None
    edges[0], edges[-1] = -np.inf, np.inf
    counts = pd.cut(values, edges, include_lowest=True).value_counts(normalize=True, sort=False)
    current = counts.to_numpy(dtype=float)
    reference = np.full(len(current), 1.0 / len(current), dtype=float)
    epsilon = 1e-6
    return float(np.sum((current - reference) * np.log((current + epsilon) / (reference + epsilon))))


def evaluate_signal_health(
    settings: Settings,
    score: pd.Series,
    *,
    signal_date: str,
    trade_date: str,
    deployment: Mapping[str, Any],
    bundle_manifest: Mapping[str, Any],
    features: pd.DataFrame | None = None,
    reference_score: pd.Series | None = None,
    require_daily_sync: bool = True,
    now_utc: datetime | None = None,
) -> SignalHealthReport:
    reasons: list[str] = []
    metrics: dict[str, Any] = {}
    production = settings.data.get("production", {})
    production = production if isinstance(production, Mapping) else {}
    dataset, dataset_sha256 = _dataset_snapshot(settings)
    metrics["datasetSha256"] = dataset_sha256
    smoke = dataset.get("smoke_test", {})
    last_date = smoke.get("last_date") if isinstance(smoke, Mapping) else None
    try:
        dataset_last_date = pd.Timestamp(last_date).normalize()
    except (TypeError, ValueError):
        dataset_last_date = pd.NaT
    if pd.isna(dataset_last_date) or dataset_last_date != pd.Timestamp(signal_date).normalize():
        reasons.append("DATASET_LAST_DATE_MISMATCH")
    if deployment.get("status") != "DEPLOYED":
        reasons.append("MODEL_NOT_DEPLOYED")
    deployment_id = deployment.get("deployment_id")
    manifest_deployment_id = bundle_manifest.get("deploymentId")
    if deployment_id and manifest_deployment_id and deployment_id != manifest_deployment_id:
        reasons.append("MODEL_REGISTRY_BUNDLE_MISMATCH")
    created = pd.Timestamp(bundle_manifest.get("createdAtUtc"))
    now = pd.Timestamp(now_utc or datetime.now(timezone.utc))
    if created.tzinfo is None:
        created = created.tz_localize("UTC")
    max_age = int(settings.data.get("production", {}).get("max_model_age_days", 45))
    age_days = (now - created).total_seconds() / 86400.0
    metrics["modelAgeDays"] = age_days
    if age_days < -1 / 24 or age_days > max_age:
        reasons.append("MODEL_STALE")
    train_end = deployment.get("train_end_date") or bundle_manifest.get("trainEndDate")
    if train_end:
        train_lag = (pd.Timestamp(signal_date).normalize() - pd.Timestamp(train_end).normalize()).days
        metrics["trainDataLagCalendarDays"] = train_lag
        max_train_lag = int(production.get("max_train_data_lag_calendar_days", 20))
        if train_lag < 0 or train_lag > max_train_lag:
            reasons.append("MODEL_TRAIN_DATA_STALE")
    elif manifest_deployment_id:
        reasons.append("MODEL_TRAIN_END_MISSING")
    values = pd.to_numeric(score, errors="coerce")
    metrics["scoreCount"] = int(len(values))
    metrics["finiteScoreCount"] = int(np.isfinite(values).sum())
    metrics["scoreStd"] = float(values.std()) if len(values) else 0.0
    if score.index.duplicated().any():
        reasons.append("DUPLICATE_INSTRUMENT")
    if values.empty or not np.isfinite(values).all():
        reasons.append("NON_FINITE_SCORE")
    if len(values) < 2 or not np.isfinite(values.std()) or float(values.std()) <= 0:
        reasons.append("DEGENERATE_SCORE")
    coverage = float(production.get("min_cross_section_coverage", 0.8))
    reference = int(bundle_manifest.get("referenceCrossSectionCount", 0))
    strategy = settings.data.get("strategy", {})
    topk = strategy.get("topk_dropout", {}) if isinstance(strategy, Mapping) else {}
    topk = topk if isinstance(topk, Mapping) else {}
    minimum = max(int(topk.get("topk", 30)) + int(topk.get("n_drop", 5)), int(np.ceil(reference * coverage)))
    metrics["minimumScoreCount"] = minimum
    if len(values) < minimum:
        reasons.append("CROSS_SECTION_TOO_SMALL")
    expected_trade_date = _expected_next_open_date(settings, signal_date)
    metrics["expectedTradeDate"] = expected_trade_date
    if expected_trade_date is None:
        reasons.append("SIGNAL_DATE_NOT_OPEN_OR_CALENDAR_MISSING")
    elif pd.Timestamp(trade_date).normalize() != pd.Timestamp(expected_trade_date):
        reasons.append("TRADE_DATE_NOT_NEXT_OPEN_DAY")
    if features is not None:
        feature_values = features.to_numpy(dtype=float)
        metrics["featureRows"] = int(len(features))
        metrics["featureColumns"] = int(features.shape[1])
        metrics["finiteFeatureRatio"] = (
            float(np.isfinite(feature_values).mean()) if feature_values.size else 0.0
        )
        if len(features) != len(values) or not feature_values.size or not np.isfinite(feature_values).all():
            reasons.append("FEATURE_COVERAGE_INVALID")
    reference_std = float(bundle_manifest.get("referenceScoreStd", float("nan")))
    reference_mean = float(bundle_manifest.get("referenceScoreMean", float("nan")))
    if np.isfinite(reference_std) and reference_std > 0 and np.isfinite(reference_mean) and len(values):
        current_std = float(values.std(ddof=0))
        dispersion_ratio = current_std / reference_std
        mean_shift = abs(float(values.mean()) - reference_mean) / reference_std
        psi = _score_psi(values, bundle_manifest.get("referenceScoreQuantiles"))
        metrics.update(
            {
                "scoreDispersionRatio": dispersion_ratio,
                "scoreMeanShiftStd": mean_shift,
                "scorePsi": psi,
            }
        )
        min_ratio = float(production.get("min_score_dispersion_ratio", 0.1))
        max_ratio = float(production.get("max_score_dispersion_ratio", 10.0))
        if not min_ratio <= dispersion_ratio <= max_ratio:
            reasons.append("SCORE_DISTRIBUTION_ABNORMAL")
        if mean_shift > float(production.get("max_score_mean_shift_std", 5.0)):
            reasons.append("SCORE_DISTRIBUTION_ABNORMAL")
        if psi is not None and psi > float(production.get("max_score_psi", 0.5)):
            reasons.append("SCORE_PSI_HIGH")
    elif manifest_deployment_id:
        reasons.append("MODEL_SCORE_REFERENCE_MISSING")
    drift = production.get("drift", {})
    drift = drift if isinstance(drift, Mapping) else {}
    if bool(drift.get("enabled", True)):
        if reference_score is None:
            metrics["driftReferenceStatus"] = "UNAVAILABLE_FIRST_SIGNAL"
        else:
            drift_metrics, drift_reasons = evaluate_signal_drift(
                reference_score,
                score,
                topk=int(topk.get("topk", 30)),
                max_score_psi=float(drift.get("max_score_psi", 0.5)),
                min_topk_overlap=float(drift.get("min_topk_overlap", 0.3)),
                max_rank_turnover=float(drift.get("max_rank_turnover", 0.25)),
            )
            metrics["driftReferenceStatus"] = "AVAILABLE"
            metrics["drift"] = drift_metrics
            reasons.extend(drift_reasons)
    if require_daily_sync:
        latest = settings.paths.state / "daily_sync" / "latest.json"
        if not latest.is_file():
            reasons.append("DAILY_SYNC_STATE_MISSING")
        else:
            sync = json.loads(latest.read_text(encoding="utf-8"))
            if sync.get("status") not in {"published", "noop"}:
                reasons.append("DAILY_SYNC_NOT_READY")
            if str(sync.get("eligible_date")) != signal_date:
                reasons.append("DAILY_SYNC_DATE_MISMATCH")
        pending = settings.paths.state / "daily_sync" / "pending_publish.json"
        if pending.is_file():
            pending_state = json.loads(pending.read_text(encoding="utf-8"))
            if pending_state.get("status") != "clear":
                reasons.append("PENDING_PUBLISH")
    reasons = list(dict.fromkeys(reasons))
    passed = not reasons
    return SignalHealthReport(
        passed=passed,
        decision="PASS" if passed else "REJECT",
        reasons=reasons,
        metrics=metrics,
    )
