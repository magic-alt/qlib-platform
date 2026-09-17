from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from qlib_platform.data.store import sha256_file
from qlib_platform.datasets.dataset_registry import DatasetRegistry
from qlib_platform.runtime.runtime_resources import resource_path
from qlib_platform.settings import Settings

SLO_POLICY_SCHEMA = "qlib-platform.slo-policy.v1"
SLO_EVALUATION_SCHEMA = "qlib-platform.slo-evaluation.v1"
SRE_EVENT_SCHEMA = "qlib-platform.sre-event.v1"
GAME_DAY_SCHEMA = "qlib-platform.game-day.v1"

_SEVERITIES = {"INFO", "WARN", "BLOCKING"}
_PROFILE_ALIASES = {
    "production": "prod",
    "prod": "prod",
    "benchmark": "benchmark",
    "bench": "benchmark",
    "dev": "dev",
    "development": "dev",
    "unspecified": "dev",
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _deep_merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], Mapping) and isinstance(value, Mapping):
            result[key] = _deep_merge(_mapping(result[key]), value)
        else:
            result[key] = value
    return result


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


@dataclass(frozen=True)
class SloPolicy:
    profile: str
    version: str
    source: str
    source_sha256: str
    effective: dict[str, Any]

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": SLO_POLICY_SCHEMA,
            "profile": self.profile,
            "version": self.version,
            "source": self.source,
            "source_sha256": self.source_sha256,
            "effective": self.effective,
        }


def load_slo_policy(settings: Settings) -> SloPolicy:
    sre_cfg = _mapping(settings.data.get("sre"))
    reference = str(sre_cfg.get("policy") or "configs/slo_policy.yaml")
    requested = Path(reference).expanduser()
    path = requested if requested.is_absolute() else resource_path(requested)
    path = path.resolve()
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, Mapping) or payload.get("schema_version") != SLO_POLICY_SCHEMA:
        raise ValueError(f"unsupported SLO policy schema: {payload.get('schema_version') if isinstance(payload, Mapping) else None}")
    profiles = _mapping(payload.get("profiles"))
    requested_profile = str(sre_cfg.get("profile") or settings.environment or "dev").strip().lower()
    profile = _PROFILE_ALIASES.get(requested_profile, requested_profile)
    if profile not in profiles:
        raise ValueError(f"SLO policy profile is not defined: {profile}")
    effective = _deep_merge(_mapping(profiles[profile]), _mapping(sre_cfg.get("policy_overrides")))
    identity = {
        "schema_version": SLO_POLICY_SCHEMA,
        "profile": profile,
        "effective": effective,
    }
    return SloPolicy(
        profile=profile,
        version=f"slo-{_canonical_sha256(identity)[:24]}",
        source=str(path),
        source_sha256=sha256_file(path),
        effective=effective,
    )


def _step_status(state: Mapping[str, Any], name: str) -> str:
    steps = state.get("steps", {})
    steps = steps if isinstance(steps, Mapping) else {}
    record = steps.get(name, {})
    record = record if isinstance(record, Mapping) else {}
    return str(record.get("status") or "")


def _sync_observed(state: Mapping[str, Any]) -> dict[str, Any]:
    steps = state.get("steps", {})
    steps = steps if isinstance(steps, Mapping) else {}
    freshness = steps.get("freshness_gate", {})
    freshness = freshness if isinstance(freshness, Mapping) else {}
    freshness_finished = str(freshness.get("finished_at_utc") or "") or None
    return {
        "required_data_complete": _step_status(state, "freshness_gate") == "SUCCEEDED"
        and _step_status(state, "qlib_publish") == "SUCCEEDED",
        "required_quality_passed": _step_status(state, "raw_validate") == "SUCCEEDED"
        and _step_status(state, "freshness_gate") == "SUCCEEDED",
        "freshness_finished_at_utc": freshness_finished,
    }


def _dataset_integrity(dataset: Mapping[str, Any]) -> bool | None:
    data_path = Path(str(dataset.get("data_path") or ""))
    manifest = data_path / "dataset_manifest.json"
    expected = str(dataset.get("dataset_manifest_sha256") or "")
    if not manifest.is_file():
        return False if expected else None
    if len(expected) != 64:
        return None
    try:
        int(expected, 16)
    except ValueError:
        return None
    return sha256_file(manifest) == expected


def _freshness_latency_minutes(
    settings: Settings,
    target_session: str,
    finished_at_utc: str | None,
) -> float | None:
    if not finished_at_utc:
        return None
    production = _mapping(settings.data.get("production"))
    daily = _mapping(production.get("daily_run"))
    schedule = _mapping(daily.get("schedule"))
    schedule_time = str(schedule.get("time") or "18:30")
    timezone_name = str(schedule.get("timezone") or "Asia/Shanghai")
    zone = ZoneInfo(timezone_name)
    target = pd.Timestamp(target_session).date()
    baseline = datetime.combine(target, datetime.strptime(schedule_time, "%H:%M").time(), tzinfo=zone)
    finished = _parse_utc(finished_at_utc).astimezone(zone)
    return (finished - baseline).total_seconds() / 60.0


def _check(
    check_id: str,
    *,
    status: str,
    severity: str,
    reason: str,
    action: str,
    observed: Any = None,
) -> dict[str, Any]:
    if severity not in _SEVERITIES:
        raise ValueError(f"unsupported SRE severity: {severity}")
    return {
        "id": check_id,
        "status": status,
        "severity": severity,
        "reason": reason,
        "first_action": action,
        "observed": observed,
    }


def evaluate_daily_slo(
    settings: Settings,
    *,
    plan: Mapping[str, Any],
    run_state: Mapping[str, Any],
    dataset: Mapping[str, Any],
    sync_state: Mapping[str, Any],
    observed: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    policy = load_slo_policy(settings)
    current = _sync_observed(sync_state)
    current.update(_mapping(observed))
    target = str(plan.get("target_session") or run_state.get("target_session") or "")
    data_complete = current.get("required_data_complete") is True
    quality_passed = current.get("required_quality_passed") is True
    integrity = current.get("dataset_integrity")
    if integrity is None:
        integrity = _dataset_integrity(dataset)

    checks: list[dict[str, Any]] = []
    checks.append(
        _check(
            "data.required_freshness",
            status="PASS" if data_complete else "FAIL",
            severity="BLOCKING",
            reason=(
                "required target-session data reached the certified freshness gate"
                if data_complete
                else "required target-session data is stale, partial, or unpublished"
            ),
            action="resume the same durable daily plan after provider/data recovery; do not run downstream research",
            observed={"target_session": target, "complete": data_complete},
        )
    )
    quality_cfg = _mapping(policy.effective.get("quality"))
    quality_severity = "BLOCKING" if bool(quality_cfg.get("required_fail_blocks", True)) else "WARN"
    checks.append(
        _check(
            "data.required_quality",
            status="PASS" if quality_passed else "FAIL",
            severity=quality_severity,
            reason=(
                "required quality gates passed"
                if quality_passed
                else "one or more required quality/freshness gates did not pass"
            ),
            action="inspect raw_validate/freshness_gate evidence and repair the immutable input before replay",
            observed={"passed": quality_passed},
        )
    )

    artifact_cfg = _mapping(policy.effective.get("artifact_verification"))
    artifact_required = bool(artifact_cfg.get("required", False))
    if integrity is True:
        integrity_status, integrity_severity = "PASS", "BLOCKING"
        integrity_reason = "published DatasetVersion manifest checksum is intact"
    elif integrity is False:
        integrity_status = "FAIL"
        integrity_severity = "BLOCKING" if artifact_required else "WARN"
        integrity_reason = "published DatasetVersion manifest is missing or checksum-mismatched"
    else:
        integrity_status, integrity_severity = "UNKNOWN", "WARN"
        integrity_reason = "DatasetVersion manifest checksum was not available as a SHA256 identity"
    checks.append(
        _check(
            "research.dataset_artifact_verification",
            status=integrity_status,
            severity=integrity_severity,
            reason=integrity_reason,
            action="run dataset-verify against the immutable DatasetVersion; never patch the manifest in place",
            observed={"integrity": integrity, "required": artifact_required},
        )
    )

    freshness_cfg = _mapping(policy.effective.get("freshness"))
    deadline = freshness_cfg.get("deadline_minutes")
    require_calibrated = bool(freshness_cfg.get("require_calibrated_deadline", False))
    latency = current.get("freshness_latency_minutes")
    if latency is None:
        latency = _freshness_latency_minutes(
            settings,
            target,
            str(current.get("freshness_finished_at_utc") or "") or None,
        )
    if deadline is None:
        calibration_status = "FAIL" if require_calibrated else "PASS"
        calibration_severity = "BLOCKING" if require_calibrated else "INFO"
        calibration_reason = (
            "production freshness deadline is not calibrated/configured"
            if require_calibrated
            else "profile permits an uncalibrated freshness deadline"
        )
        checks.append(
            _check(
                "policy.freshness_deadline_calibration",
                status=calibration_status,
                severity=calibration_severity,
                reason=calibration_reason,
                action="calibrate from observed provider/runtime distributions and pin policy_overrides.freshness.deadline_minutes",
                observed={"deadline_minutes": None},
            )
        )
    else:
        numeric_deadline = float(deadline)
        deadline_ok = latency is not None and float(latency) <= numeric_deadline
        checks.append(
            _check(
                "data.freshness_deadline",
                status="PASS" if deadline_ok else "FAIL",
                severity="BLOCKING",
                reason=(
                    "freshness gate completed within the configured deadline"
                    if deadline_ok
                    else "freshness gate missed the configured deadline or has no completion timestamp"
                ),
                action="inspect provider latency and catch up the durable plan; do not relax the threshold during the incident",
                observed={"latency_minutes": latency, "deadline_minutes": numeric_deadline},
            )
        )

    blocking = [
        str(item["id"])
        for item in checks
        if item["status"] == "FAIL" and item["severity"] == "BLOCKING"
    ]
    degraded = [
        str(item["id"])
        for item in checks
        if item["status"] in {"FAIL", "UNKNOWN"} and item["severity"] != "INFO"
    ]
    return {
        "schema_version": SLO_EVALUATION_SCHEMA,
        "phase": "pre-research",
        "policy": policy.snapshot(),
        "policy_version": policy.version,
        "profile": policy.profile,
        "target_session": target,
        "run_id": run_state.get("run_id"),
        "generated_at_utc": _now_utc().isoformat(),
        "checks": checks,
        "allow_downstream": not blocking,
        "status": "BLOCKED" if blocking else ("DEGRADED" if degraded else "HEALTHY"),
        "blocking_reasons": blocking,
    }


def evaluate_run_completion(settings: Settings, manifest: Mapping[str, Any]) -> dict[str, Any]:
    policy = load_slo_policy(settings)
    status = str(manifest.get("status") or "UNKNOWN")
    report = Path(str(manifest.get("report") or ""))
    plan = Path(str(manifest.get("plan") or ""))
    successful = status == "SUCCEEDED"
    artifacts_ok = not successful or (report.is_file() and plan.is_file())
    notification = _step_status(manifest, "notification")
    slo_step = _mapping(_mapping(manifest.get("steps")).get("slo_gate"))
    slo_output = _mapping(slo_step.get("output"))
    policy_bound = bool(slo_output.get("policy_version"))
    checks = [
        _check(
            "research.daily_run_artifacts",
            status="PASS" if artifacts_ok else "FAIL",
            severity="BLOCKING",
            reason=(
                "successful DailyRun has its declared report/plan artifacts"
                if artifacts_ok
                else "successful DailyRun is missing a declared report or plan artifact"
            ),
            action="verify the run evidence and replay from the immutable plan; do not fabricate missing artifacts",
            observed={"run_status": status, "report": str(report), "plan": str(plan)},
        ),
        _check(
            "research.slo_policy_lineage",
            status="PASS" if policy_bound else "FAIL",
            severity="BLOCKING",
            reason="SLO policy version is bound to the DailyRun" if policy_bound else "SLO policy lineage is missing",
            action="replay the DailyRun through the SRE-aware entrypoint so policy identity is persisted",
            observed={"policy_version": slo_output.get("policy_version")},
        ),
        _check(
            "platform.notification_delivery",
            status="PASS" if notification in {"SUCCEEDED", "SKIPPED"} else "FAIL",
            severity="WARN",
            reason=(
                "notification path succeeded or was explicitly disabled"
                if notification in {"SUCCEEDED", "SKIPPED"}
                else "notification delivery did not complete"
            ),
            action="preserve run state and retry only the notification/delivery path",
            observed={"status": notification},
        ),
    ]
    blocking = [item["id"] for item in checks if item["status"] == "FAIL" and item["severity"] == "BLOCKING"]
    return {
        "schema_version": SLO_EVALUATION_SCHEMA,
        "phase": "completion",
        "policy": policy.snapshot(),
        "policy_version": policy.version,
        "profile": policy.profile,
        "target_session": manifest.get("target_session"),
        "run_id": manifest.get("run_id"),
        "generated_at_utc": _now_utc().isoformat(),
        "checks": checks,
        "allow_downstream": not blocking,
        "status": "BLOCKED" if blocking else "HEALTHY",
        "blocking_reasons": blocking,
    }


def evaluate_baseline_metric_drift(
    *,
    metric: str,
    reference: float,
    current: float,
    warn_relative: float,
    reject_relative: float,
) -> dict[str, Any]:
    denominator = abs(reference) if reference else 1.0
    relative = abs(current - reference) / denominator
    if relative >= reject_relative:
        decision = "REJECT"
    elif relative >= warn_relative:
        decision = "INVESTIGATE"
    else:
        decision = "PASS"
    return {
        "metric": metric,
        "reference": reference,
        "current": current,
        "relative_drift": relative,
        "decision": decision,
        "model_action": "NONE",
        "automatic_actions": [],
        "note": "baseline drift is monitoring evidence only; it cannot retrain, select, promote, or deploy models",
    }


def _evaluation_path(settings: Settings, evaluation: Mapping[str, Any]) -> Path:
    session = str(evaluation.get("target_session") or "unknown")
    run_id = str(evaluation.get("run_id") or "run")
    phase = str(evaluation.get("phase") or "evaluation")
    return settings.paths.state / "sre" / "evaluations" / session / f"{run_id}-{phase}.json"


def write_slo_evaluation(settings: Settings, evaluation: Mapping[str, Any]) -> Path:
    path = _evaluation_path(settings, evaluation)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(dict(evaluation), ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def alert_fingerprint(check: Mapping[str, Any], context: Mapping[str, Any]) -> str:
    identity = {
        "provider": context.get("provider"),
        "session": context.get("session"),
        "failed_gate": check.get("id"),
    }
    return f"alert-{_canonical_sha256(identity)[:24]}"


class SreEventStore:
    def __init__(self, root: Path) -> None:
        self.path = root / "sre" / "events.jsonl"

    def events(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        events: list[dict[str, Any]] = []
        for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"SRE event ledger is corrupt at line {number}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"SRE event ledger entry is not an object at line {number}")
            events.append(payload)
        return events

    def active_incidents(self) -> dict[str, dict[str, Any]]:
        active: dict[str, dict[str, Any]] = {}
        for event in self.events():
            fingerprint = str(event.get("fingerprint") or "")
            if not fingerprint:
                continue
            if event.get("event_type") == "ALERT_OPEN":
                active[fingerprint] = event
            elif event.get("event_type") == "RESOLVED":
                active.pop(fingerprint, None)
        return active

    def append(self, event: Mapping[str, Any]) -> dict[str, Any]:
        payload = {
            "schema_version": SRE_EVENT_SCHEMA,
            "event_id": f"sre-{uuid.uuid4().hex}",
            "occurred_at_utc": _now_utc().isoformat(),
            **dict(event),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        return payload

    def sync_evaluation(
        self,
        evaluation: Mapping[str, Any],
        *,
        context: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        active = self.active_incidents()
        appended: list[dict[str, Any]] = []
        current_checks = {
            str(check.get("id")): check
            for check in evaluation.get("checks", [])
            if isinstance(check, Mapping)
        }
        for check in current_checks.values():
            if check.get("status") not in {"FAIL", "UNKNOWN"} or check.get("severity") == "INFO":
                continue
            fingerprint = alert_fingerprint(check, context)
            if fingerprint in active:
                continue
            correlation = f"incident-{uuid.uuid4().hex[:16]}"
            event = self.append(
                {
                    "event_type": "ALERT_OPEN",
                    "fingerprint": fingerprint,
                    "incident_correlation_id": correlation,
                    "severity": check.get("severity"),
                    "run_id": context.get("run_id"),
                    "session": context.get("session"),
                    "release": context.get("release"),
                    "provider": context.get("provider"),
                    "failed_gate": check.get("id"),
                    "reason": check.get("reason"),
                    "first_action": check.get("first_action"),
                    "policy_version": evaluation.get("policy_version"),
                }
            )
            appended.append(event)
            active[fingerprint] = event

        for fingerprint, event in list(active.items()):
            if event.get("session") != context.get("session") or event.get("provider") != context.get("provider"):
                continue
            gate = str(event.get("failed_gate") or "")
            check = current_checks.get(gate)
            if check is None or check.get("status") != "PASS":
                continue
            appended.append(
                self.append(
                    {
                        "event_type": "RESOLVED",
                        "fingerprint": fingerprint,
                        "incident_correlation_id": event.get("incident_correlation_id"),
                        "severity": "INFO",
                        "run_id": context.get("run_id"),
                        "session": context.get("session"),
                        "release": context.get("release"),
                        "provider": context.get("provider"),
                        "failed_gate": gate,
                        "reason": "the previously failing SLO gate now passes",
                        "first_action": "resume normal operation after verifying replay evidence",
                        "policy_version": evaluation.get("policy_version"),
                    }
                )
            )
        return appended


def create_override(
    settings: Settings,
    *,
    gate: str,
    operator: str,
    reason: str,
    expires_at: str,
    scope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not operator.strip():
        raise ValueError("override operator is required")
    if not reason.strip():
        raise ValueError("override reason is required")
    expiry = _parse_utc(expires_at)
    if expiry <= _now_utc():
        raise ValueError("override expiry must be in the future")
    payload = {
        "override_id": f"override-{uuid.uuid4().hex}",
        "gate": gate,
        "operator": operator,
        "reason": reason,
        "expires_at_utc": expiry.isoformat(),
        "scope": dict(scope or {}),
        "created_at_utc": _now_utc().isoformat(),
    }
    root = settings.paths.state / "sre" / "overrides"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{payload['override_id']}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    SreEventStore(settings.paths.state).append(
        {
            "event_type": "OVERRIDE_CREATED",
            "severity": "WARN",
            "fingerprint": f"override-{_canonical_sha256(payload)[:24]}",
            "incident_correlation_id": None,
            "failed_gate": gate,
            "operator": operator,
            "reason": reason,
            "expires_at_utc": expiry.isoformat(),
            "first_action": "review the override before expiry; overrides never rewrite immutable evidence",
        }
    )
    return {**payload, "path": str(path)}


def active_overrides(settings: Settings) -> list[dict[str, Any]]:
    root = settings.paths.state / "sre" / "overrides"
    now = _now_utc()
    result = []
    for path in sorted(root.glob("*.json")) if root.is_dir() else []:
        payload = _safe_json(path)
        raw_expiry = str(payload.get("expires_at_utc") or "")
        if raw_expiry and _parse_utc(raw_expiry) > now:
            result.append({**payload, "path": str(path)})
    return result


def _latest_json(root: Path, filename: str) -> tuple[Path | None, dict[str, Any]]:
    if not root.is_dir():
        return None, {}
    candidates = [path for path in root.glob(f"*/{filename}") if path.is_file()]
    if not candidates:
        return None, {}
    latest = max(candidates, key=lambda path: path.stat().st_mtime_ns)
    return latest, _safe_json(latest)


def _missed_sessions(settings: Settings, latest_run: Mapping[str, Any]) -> list[str]:
    calendar_path = settings.paths.metadata / "trade_calendar.parquet"
    if not calendar_path.is_file():
        return []
    try:
        calendar = pd.read_parquet(calendar_path)
    except (OSError, ValueError):
        return []
    if not {"cal_date", "is_open"}.issubset(calendar.columns):
        return []
    dates = pd.to_datetime(calendar.loc[calendar["is_open"].astype(int).eq(1), "cal_date"], errors="coerce")
    today = pd.Timestamp.now(tz="Asia/Shanghai").tz_localize(None).normalize()
    dates = dates.dropna().dt.normalize()
    dates = dates[dates <= today]
    if dates.empty:
        return []
    latest_target = str(latest_run.get("target_session") or "")
    if not latest_target:
        return [value.strftime("%Y%m%d") for value in dates.tail(20)]
    after = dates[dates > pd.Timestamp(latest_target).normalize()]
    return [value.strftime("%Y%m%d") for value in after.tail(20)]


def collect_sre_status(settings: Settings) -> dict[str, Any]:
    policy = load_slo_policy(settings)
    plan_path, plan = _latest_json(settings.paths.state / "daily_sync" / "plans", "plan.json")
    run_path, run = _latest_json(settings.paths.state / "daily_run" / "runs", "manifest.json")
    eval_root = settings.paths.state / "sre" / "evaluations"
    evaluation_files = [path for path in eval_root.rglob("*.json") if path.is_file()] if eval_root.is_dir() else []
    evaluation_path = max(evaluation_files, key=lambda path: path.stat().st_mtime_ns) if evaluation_files else None
    evaluation = _safe_json(evaluation_path) if evaluation_path else {}

    active_dataset: dict[str, Any] = {}
    try:
        registered = DatasetRegistry(settings.registry_path).inspect(settings.qlib_dataset_ref)
    except (OSError, ValueError):
        registered = None
    if registered is not None:
        dataset_manifest = _safe_json(registered.manifest_path)
        semantic = _mapping(dataset_manifest.get("semantic_contract"))
        active_dataset = {
            "reference": settings.qlib_dataset_ref,
            "version_id": registered.version_id,
            "data_release_id": dataset_manifest.get("data_release_id") or semantic.get("data_release_id"),
            "manifest": str(registered.manifest_path),
        }

    capacity = shutil.disk_usage(settings.paths.root if settings.paths.root.exists() else settings.paths.root.parent)
    temp_roots = [settings.paths.state, settings.paths.output]
    orphan_temps = sum(
        1
        for root in temp_roots
        if root.is_dir()
        for path in root.rglob("*.tmp")
        if path.is_file()
    )
    active_alerts: list[dict[str, Any]] = []
    ledger_error = None
    try:
        active_alerts = list(SreEventStore(settings.paths.state).active_incidents().values())
    except ValueError as exc:
        ledger_error = str(exc)

    missed = _missed_sessions(settings, run)
    blockers = list(evaluation.get("blocking_reasons", [])) if isinstance(evaluation.get("blocking_reasons"), list) else []
    schedule_cfg = _mapping(policy.effective.get("schedule"))
    if missed and str(schedule_cfg.get("missed_session_severity") or "WARN") == "BLOCKING":
        blockers.append("platform.missed_schedule")
    capacity_cfg = _mapping(policy.effective.get("capacity"))
    min_free = capacity_cfg.get("min_free_bytes")
    if min_free is not None and capacity.free < int(min_free):
        blockers.append("platform.capacity")
    if ledger_error:
        blockers.append("platform.sre_event_ledger")

    return {
        "schema_version": "qlib-platform.sre-status.v1",
        "status": "BLOCKED" if blockers else ("DEGRADED" if active_alerts or missed else "HEALTHY"),
        "policy": policy.snapshot(),
        "recent_session": run.get("target_session") or plan.get("target_session"),
        "watermarks": plan.get("watermarks", {}),
        "active_release": active_dataset.get("data_release_id"),
        "active_dataset": active_dataset,
        "recent_daily_run": {"path": str(run_path) if run_path else None, **run} if run else None,
        "latest_slo": {"path": str(evaluation_path), **evaluation} if evaluation_path else None,
        "blocking_reasons": sorted(set(str(value) for value in blockers)),
        "missed_sessions": missed,
        "active_alerts": active_alerts,
        "active_overrides": active_overrides(settings),
        "platform": {
            "disk_free_bytes": capacity.free,
            "disk_total_bytes": capacity.total,
            "orphan_temporary_artifacts": orphan_temps,
            "event_ledger_error": ledger_error,
        },
        "sources": {
            "latest_plan": str(plan_path) if plan_path else None,
            "latest_run": str(run_path) if run_path else None,
        },
    }


def render_sre_status(payload: Mapping[str, Any]) -> str:
    policy = _mapping(payload.get("policy"))
    return "\n".join(
        [
            "qlib-platform SRE status",
            "",
            f"Status            {payload.get('status', 'UNKNOWN')}",
            f"Recent session    {payload.get('recent_session') or 'N/A'}",
            f"Active release    {payload.get('active_release') or 'N/A'}",
            f"SLO profile       {policy.get('profile') or 'N/A'}",
            f"SLO version       {policy.get('version') or 'N/A'}",
            f"Missed sessions   {len(payload.get('missed_sessions', []))}",
            f"Open alerts       {len(payload.get('active_alerts', []))}",
            f"Blocking reasons  {', '.join(payload.get('blocking_reasons', [])) or 'none'}",
        ]
    )


_GAME_DAY_GATES = {
    "provider-late": "data.required_freshness",
    "provider-429": "data.required_freshness",
    "schema-drift": "data.required_quality",
    "endpoint-missing": "data.required_quality",
    "qlib-corrupt": "research.dataset_artifact_verification",
    "disk-full": "platform.capacity",
    "process-kill": "platform.run_recovery",
    "pointer-crash": "platform.release_activation",
    "alert-destination-unavailable": "platform.notification_delivery",
    "long-backfill": "platform.missed_schedule",
}


def run_game_day_fixture(
    settings: Settings,
    *,
    scenario: str,
    output: Path,
) -> Path:
    if scenario not in _GAME_DAY_GATES:
        raise ValueError(f"unsupported game-day scenario: {scenario}")
    policy = load_slo_policy(settings)
    gate = _GAME_DAY_GATES[scenario]
    severity = "WARN" if scenario == "alert-destination-unavailable" else "BLOCKING"
    session = "20990102"
    run_id = f"game-day-{scenario}"
    context = {
        "run_id": run_id,
        "session": session,
        "release": "fixture-release",
        "provider": "fixture-provider",
    }
    failed_check = _check(
        gate,
        status="FAIL",
        severity=severity,
        reason=f"injected game-day failure: {scenario}",
        action="execute the scenario runbook, recover the dependency/state, and replay the same immutable input",
        observed={"scenario": scenario, "injected": True},
    )
    failed = {
        "schema_version": SLO_EVALUATION_SCHEMA,
        "phase": "game-day-failure",
        "policy": policy.snapshot(),
        "policy_version": policy.version,
        "profile": policy.profile,
        "target_session": session,
        "run_id": run_id,
        "generated_at_utc": _now_utc().isoformat(),
        "checks": [failed_check],
        "allow_downstream": severity != "BLOCKING",
        "status": "BLOCKED" if severity == "BLOCKING" else "DEGRADED",
        "blocking_reasons": [gate] if severity == "BLOCKING" else [],
    }
    store = SreEventStore(settings.paths.state)
    first_events = store.sync_evaluation(failed, context=context)
    replay_events = store.sync_evaluation(failed, context=context)
    recovered_check = {**failed_check, "status": "PASS", "reason": "injected failure recovered"}
    recovered = {
        **failed,
        "phase": "game-day-recovery",
        "generated_at_utc": _now_utc().isoformat(),
        "checks": [recovered_check],
        "allow_downstream": True,
        "status": "HEALTHY",
        "blocking_reasons": [],
    }
    resolved_events = store.sync_evaluation(recovered, context=context)
    correlation = next(
        (event.get("incident_correlation_id") for event in first_events if event.get("event_type") == "ALERT_OPEN"),
        None,
    )
    resolved_correlation = next(
        (
            event.get("incident_correlation_id")
            for event in resolved_events
            if event.get("event_type") == "RESOLVED"
        ),
        None,
    )
    report = {
        "schema_version": GAME_DAY_SCHEMA,
        "scenario": scenario,
        "policy_version": policy.version,
        "failed_evaluation": failed,
        "recovered_evaluation": recovered,
        "failure_alerts": first_events,
        "replay_alerts": replay_events,
        "resolved_events": resolved_events,
        "audit": {
            "downstream_blocked_on_failure": severity == "BLOCKING" and not failed["allow_downstream"],
            "alert_deduplicated_on_replay": not replay_events,
            "resolved_event_emitted": any(event.get("event_type") == "RESOLVED" for event in resolved_events),
            "incident_correlation_preserved": bool(correlation) and correlation == resolved_correlation,
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"game-day-{scenario}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path
