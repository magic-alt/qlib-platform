from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

import numpy as np
import pandas as pd

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


def evaluate_signal_health(
    settings: Settings,
    score: pd.Series,
    *,
    signal_date: str,
    trade_date: str,
    deployment: Mapping[str, Any],
    bundle_manifest: Mapping[str, Any],
    require_daily_sync: bool = True,
    now_utc: datetime | None = None,
) -> SignalHealthReport:
    reasons: list[str] = []
    metrics: dict[str, Any] = {}
    dataset, dataset_sha256 = _dataset_snapshot(settings)
    metrics["datasetSha256"] = dataset_sha256
    smoke = dataset.get("smoke_test", {})
    last_date = smoke.get("last_date") if isinstance(smoke, Mapping) else None
    if str(last_date) != signal_date:
        reasons.append("DATASET_LAST_DATE_MISMATCH")
    if deployment.get("status") != "DEPLOYED":
        reasons.append("MODEL_NOT_DEPLOYED")
    created = pd.Timestamp(bundle_manifest.get("createdAtUtc"))
    now = pd.Timestamp(now_utc or datetime.now(timezone.utc))
    if created.tzinfo is None:
        created = created.tz_localize("UTC")
    max_age = int(settings.data.get("production", {}).get("max_model_age_days", 45))
    age_days = (now - created).total_seconds() / 86400.0
    metrics["modelAgeDays"] = age_days
    if age_days < -1 / 24 or age_days > max_age:
        reasons.append("MODEL_STALE")
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
    production = settings.data.get("production", {})
    production = production if isinstance(production, Mapping) else {}
    coverage = float(production.get("min_cross_section_coverage", 0.8))
    reference = int(bundle_manifest.get("referenceCrossSectionCount", 0))
    strategy = settings.data.get("strategy", {})
    topk = strategy.get("topk_dropout", {}) if isinstance(strategy, Mapping) else {}
    topk = topk if isinstance(topk, Mapping) else {}
    minimum = max(int(topk.get("topk", 30)) + int(topk.get("n_drop", 5)), int(np.ceil(reference * coverage)))
    metrics["minimumScoreCount"] = minimum
    if len(values) < minimum:
        reasons.append("CROSS_SECTION_TOO_SMALL")
    if pd.Timestamp(trade_date) <= pd.Timestamp(signal_date):
        reasons.append("INVALID_TRADE_DATE")
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
    passed = not reasons
    return SignalHealthReport(
        passed=passed,
        decision="PASS" if passed else "REJECT",
        reasons=reasons,
        metrics=metrics,
    )
