from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from qlib_platform.runtime.sre import (
    SRE_EVENT_SCHEMA,
    SreEventStore,
    active_overrides,
    collect_sre_status,
    create_override,
    evaluate_baseline_metric_drift,
    evaluate_daily_slo,
    load_slo_policy,
    render_sre_status,
    run_game_day_fixture,
    write_slo_evaluation,
)
from qlib_platform.settings import Paths, Settings


def _settings(
    tmp_path: Path,
    *,
    environment: str = "dev",
    policy_overrides: dict[str, object] | None = None,
) -> Settings:
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = tmp_path / "pipeline.yaml"
    config.write_text("mode: standalone\n", encoding="utf-8")
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    data: dict[str, object] = {
        "mode": "standalone",
        "environment": environment,
        "project_root": str(paths.root),
        "qlib": {"dataset_ref": "standalone-current"},
        "data_source": {"kind": "fixture"},
        "production": {
            "daily_run": {
                "schedule": {"time": "18:30", "timezone": "Asia/Shanghai"},
            }
        },
        "sre": {},
    }
    if policy_overrides:
        data["sre"] = {"policy_overrides": policy_overrides}
    return Settings(
        config_path=config,
        data=data,  # type: ignore[arg-type]
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=paths.qlib_versions / "dv1",
    )


def _plan() -> dict[str, object]:
    return {
        "plan_id": "syncplan-20260917-fixture",
        "target_session": "20260917",
        "required_endpoints": ["daily", "adj_factor", "daily_basic"],
        "watermarks": {"daily": {"last_success": "20260916"}},
    }


def _dataset(tmp_path: Path, *, valid_hash: bool = False) -> dict[str, object]:
    root = tmp_path / "dataset"
    root.mkdir(exist_ok=True)
    manifest = root / "dataset_manifest.json"
    manifest.write_text('{"version_id":"dv1"}', encoding="utf-8")
    digest = None
    if valid_hash:
        from qlib_platform.data.store import sha256_file

        digest = sha256_file(manifest)
    return {
        "dataset_version_id": "dv1",
        "data_release_id": "dr1",
        "data_path": str(root),
        "dataset_manifest_sha256": digest or "symbolic-fixture",
    }


def _sync_state(*, passed: bool = True) -> dict[str, object]:
    status = "SUCCEEDED" if passed else "FAILED"
    return {
        "steps": {
            "raw_validate": {"status": status},
            "freshness_gate": {
                "status": status,
                "finished_at_utc": "2026-09-17T11:00:00+00:00",
            },
            "qlib_publish": {"status": status},
        }
    }


def test_policy_version_changes_with_threshold_override(tmp_path: Path) -> None:
    base = load_slo_policy(_settings(tmp_path / "base"))
    calibrated = load_slo_policy(
        _settings(
            tmp_path / "calibrated",
            policy_overrides={"freshness": {"deadline_minutes": 90}},
        )
    )
    assert base.version.startswith("slo-")
    assert calibrated.version != base.version
    assert calibrated.effective["freshness"]["deadline_minutes"] == 90
    assert base.snapshot()["source_sha256"]


def test_prod_requires_calibrated_freshness_deadline(tmp_path: Path) -> None:
    settings = _settings(tmp_path, environment="prod")
    evaluation = evaluate_daily_slo(
        settings,
        plan=_plan(),
        run_state={"run_id": "daily-fixture", "target_session": "20260917"},
        dataset=_dataset(tmp_path, valid_hash=True),
        sync_state=_sync_state(),
        observed={"required_data_complete": True, "required_quality_passed": True},
    )
    calibration = next(
        item for item in evaluation["checks"] if item["id"] == "policy.freshness_deadline_calibration"
    )
    assert calibration["status"] == "FAIL"
    assert calibration["severity"] == "BLOCKING"
    assert evaluation["allow_downstream"] is False


def test_stale_or_partial_data_blocks_downstream(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    evaluation = evaluate_daily_slo(
        settings,
        plan=_plan(),
        run_state={"run_id": "daily-fixture", "target_session": "20260917"},
        dataset=_dataset(tmp_path),
        sync_state=_sync_state(passed=False),
        observed={"required_data_complete": False, "required_quality_passed": False},
    )
    assert evaluation["status"] == "BLOCKED"
    assert evaluation["allow_downstream"] is False
    assert "data.required_freshness" in evaluation["blocking_reasons"]
    assert "data.required_quality" in evaluation["blocking_reasons"]


def test_calibrated_deadline_is_evaluated_without_mutating_policy(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        policy_overrides={"freshness": {"deadline_minutes": 30}},
    )
    late = evaluate_daily_slo(
        settings,
        plan=_plan(),
        run_state={"run_id": "daily-fixture"},
        dataset=_dataset(tmp_path, valid_hash=True),
        sync_state=_sync_state(),
        observed={
            "required_data_complete": True,
            "required_quality_passed": True,
            "freshness_latency_minutes": 45.0,
        },
    )
    deadline = next(item for item in late["checks"] if item["id"] == "data.freshness_deadline")
    assert deadline["status"] == "FAIL"
    assert deadline["observed"] == {"latency_minutes": 45.0, "deadline_minutes": 30.0}
    assert load_slo_policy(settings).effective["freshness"]["deadline_minutes"] == 30


def test_alert_fingerprint_deduplicates_and_emits_resolved(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    failed = evaluate_daily_slo(
        settings,
        plan=_plan(),
        run_state={"run_id": "daily-fixture"},
        dataset=_dataset(tmp_path, valid_hash=True),
        sync_state=_sync_state(passed=False),
        observed={"required_data_complete": False, "required_quality_passed": True},
    )
    store = SreEventStore(settings.paths.state)
    context = {
        "run_id": "daily-fixture",
        "session": "20260917",
        "release": "dr1",
        "provider": "fixture",
    }
    first = store.sync_evaluation(failed, context=context)
    second = store.sync_evaluation(failed, context=context)
    assert any(item["event_type"] == "ALERT_OPEN" for item in first)
    assert second == []

    recovered = evaluate_daily_slo(
        settings,
        plan=_plan(),
        run_state={"run_id": "daily-fixture"},
        dataset=_dataset(tmp_path, valid_hash=True),
        sync_state=_sync_state(),
        observed={"required_data_complete": True, "required_quality_passed": True},
    )
    resolved = store.sync_evaluation(recovered, context=context)
    assert any(item["event_type"] == "RESOLVED" for item in resolved)
    events = store.events()
    assert all(item["schema_version"] == SRE_EVENT_SCHEMA for item in events)
    open_event = next(item for item in events if item["event_type"] == "ALERT_OPEN")
    resolved_event = next(item for item in events if item["event_type"] == "RESOLVED")
    assert open_event["incident_correlation_id"] == resolved_event["incident_correlation_id"]
    assert open_event["first_action"]
    assert not store.active_incidents()


def test_event_ledger_corruption_fails_closed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = SreEventStore(settings.paths.state)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{broken\n", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt"):
        store.events()


def test_override_requires_operator_reason_and_future_expiry(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    with pytest.raises(ValueError, match="operator"):
        create_override(
            settings, gate="platform.capacity", operator="", reason="maintenance", expires_at=future
        )
    with pytest.raises(ValueError, match="reason"):
        create_override(settings, gate="platform.capacity", operator="alice", reason="", expires_at=future)
    with pytest.raises(ValueError, match="future"):
        create_override(
            settings,
            gate="platform.capacity",
            operator="alice",
            reason="maintenance",
            expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        )

    override = create_override(
        settings,
        gate="platform.capacity",
        operator="alice",
        reason="temporary storage migration",
        expires_at=future,
        scope={"session": "20260917"},
    )
    assert override["operator"] == "alice"
    assert override["reason"]
    assert Path(override["path"]).is_file()
    assert len(active_overrides(settings)) == 1
    audit = SreEventStore(settings.paths.state).events()[-1]
    assert audit["event_type"] == "OVERRIDE_CREATED"
    assert audit["expires_at_utc"]


def test_baseline_drift_is_monitoring_only() -> None:
    result = evaluate_baseline_metric_drift(
        metric="icir",
        reference=0.50,
        current=0.20,
        warn_relative=0.20,
        reject_relative=0.40,
    )
    assert result["decision"] == "REJECT"
    assert result["model_action"] == "NONE"
    assert result["automatic_actions"] == []
    assert "retrain" in result["note"]


def test_slo_evaluation_artifact_is_machine_readable(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    evaluation = evaluate_daily_slo(
        settings,
        plan=_plan(),
        run_state={"run_id": "daily-fixture"},
        dataset=_dataset(tmp_path),
        sync_state=_sync_state(),
        observed={"required_data_complete": True, "required_quality_passed": True},
    )
    path = write_slo_evaluation(settings, evaluation)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "qlib-platform.slo-evaluation.v1"
    assert payload["policy_version"] == load_slo_policy(settings).version
    assert payload["checks"]


def test_status_is_read_only_and_surfaces_missed_schedule(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    calendar = settings.paths.metadata / "trade_calendar.parquet"
    pd.DataFrame(
        {
            "cal_date": ["2026-09-15", "2026-09-16", "2026-09-17"],
            "is_open": [1, 1, 1],
        }
    ).to_parquet(calendar, index=False)
    run_root = settings.paths.state / "daily_run" / "runs" / "run-1"
    run_root.mkdir(parents=True)
    (run_root / "manifest.json").write_text(
        json.dumps({"target_session": "20260915", "status": "SUCCEEDED"}),
        encoding="utf-8",
    )
    before = sorted(str(path.relative_to(settings.paths.root)) for path in settings.paths.root.rglob("*"))
    status = collect_sre_status(settings)
    after = sorted(str(path.relative_to(settings.paths.root)) for path in settings.paths.root.rglob("*"))
    assert before == after
    assert status["recent_session"] == "20260915"
    assert "20260916" in status["missed_sessions"]
    assert "20260917" in status["missed_sessions"]
    assert "SLO version" in render_sre_status(status)


def test_status_surfaces_event_ledger_corruption(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = SreEventStore(settings.paths.state)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("not-json\n", encoding="utf-8")
    status = collect_sre_status(settings)
    assert status["status"] == "BLOCKED"
    assert "platform.sre_event_ledger" in status["blocking_reasons"]
    assert status["platform"]["event_ledger_error"]


@pytest.mark.parametrize(
    "scenario",
    [
        "provider-late",
        "provider-429",
        "schema-drift",
        "endpoint-missing",
        "qlib-corrupt",
        "disk-full",
        "process-kill",
        "pointer-crash",
        "alert-destination-unavailable",
        "long-backfill",
    ],
)
def test_game_day_failure_alert_replay_recovery_resolved(tmp_path: Path, scenario: str) -> None:
    settings = _settings(tmp_path / scenario)
    path = run_game_day_fixture(settings, scenario=scenario, output=tmp_path / "evidence" / scenario)
    report = json.loads(path.read_text(encoding="utf-8"))
    audit = report["audit"]
    assert audit["alert_deduplicated_on_replay"] is True
    assert audit["resolved_event_emitted"] is True
    assert audit["incident_correlation_preserved"] is True
    if scenario != "alert-destination-unavailable":
        assert audit["downstream_blocked_on_failure"] is True
    assert report["failure_alerts"][0]["first_action"]


def test_game_day_rejects_unknown_scenario(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with pytest.raises(ValueError, match="unsupported"):
        run_game_day_fixture(settings, scenario="unknown", output=tmp_path / "evidence")


def test_base_daily_runner_blocks_regression_when_consumer_gate_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qlib_platform.runtime import daily_research_run as daily

    settings = _settings(tmp_path)

    class FakeSync:
        def load_plan(self, plan_id: str):
            raise AssertionError("load_plan must be patched by the test")

        def _plan_path(self, plan_id: str) -> Path:
            raise AssertionError("_plan_path must be patched by the test")

        def apply_plan(self, *args, **kwargs):
            raise AssertionError("apply_plan must be patched by the test")

    monkeypatch.setattr(daily, "PlannedDailySyncService", lambda current: FakeSync())
    runner = daily.DailyResearchRun(settings)
    plan = {
        "plan_id": "plan-fixture",
        "target_session": "20260916",
        "config_sha256": "cfg",
        "status": "PLANNED",
    }
    monkeypatch.setattr(runner.sync, "load_plan", lambda plan_id: plan)
    monkeypatch.setattr(runner.sync, "_plan_path", lambda plan_id: tmp_path / "plan.json")
    monkeypatch.setattr(runner.sync, "apply_plan", lambda *args, **kwargs: tmp_path / "apply.json")
    monkeypatch.setattr(daily, "_session_ready", lambda *args, **kwargs: (True, None))
    monkeypatch.setattr(runner, "_verify_dataset", lambda *args, **kwargs: _dataset(tmp_path))
    monkeypatch.setattr(
        runner,
        "_post_dataset_gate",
        lambda *args, **kwargs: (
            False,
            "data.required_freshness",
            {"status": "BLOCKED", "policy_version": "slo-fixture"},
        ),
    )
    regression_called = False

    def regression(*args, **kwargs):
        nonlocal regression_called
        regression_called = True
        return {}

    monkeypatch.setattr(runner, "_run_regression", regression)
    path = runner.execute_plan("plan-fixture")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED"
    assert payload["steps"]["slo_gate"]["status"] == "BLOCKED"
    assert payload["steps"]["regression_backtest"]["status"] == "BLOCKED"
    assert regression_called is False
