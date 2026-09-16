from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from qlib_platform.data.audited_daily_sync import (
    CHECKPOINT_CONTRACT_VERSION,
    AuditedResumableDailySyncService,
)
from qlib_platform.data.planned_daily_sync import SyncPlanInvalidatedError
from qlib_platform.data.resumable_certified_sync import ResumableCertifiedDailySyncService
from qlib_platform.data.store import sha256_file
from qlib_platform.runtime import daily_research_run as base
from qlib_platform.runtime.production_daily_run import (
    DAILY_RUN_CONTRACT_VERSION,
    DailyResearchRun,
)
from qlib_platform.settings import Paths, Settings


def _settings(tmp_path: Path) -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    config = tmp_path / "configs" / "pipeline.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("data_source:\n  kind: tushare\n", encoding="utf-8")
    return Settings(
        config_path=config,
        data={
            "start_date": "20260810",
            "end_date": "20260811",
            "data_source": {
                "kind": "tushare",
                "optional_endpoints": {
                    "moneyflow": False,
                    "stk_limit": False,
                    "suspend_d": False,
                    "stock_st": False,
                },
            },
            "qlib": {
                "dataset_dir": "unused",
                "dataset_name": "test",
                "dataset_version": "test",
                "dataset_ref": "test-current",
                "include_fields": [],
            },
            "data_sync": {
                "timezone": "Asia/Shanghai",
                "ready_after": "17:30",
                "market_lookback_trading_days": 2,
                "market_catchup_trading_days": 10,
                "corporate_action_lookback_calendar_days": 2,
            },
            "research": {"benchmark": "SH000300"},
            "universe": {"instruments": "all"},
            "production": {
                "daily_run": {
                    "notify": False,
                    "regression": {"enabled": False},
                    "verification": {"mode": "sampled", "sample_size": 8, "workers": 1},
                }
            },
        },
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=paths.root / "qlib" / "test",
    )


def _calendar(settings: Settings) -> None:
    pd.DataFrame(
        {
            "cal_date": pd.to_datetime(["2026-08-10", "2026-08-11"]),
            "is_open": [1, 1],
            "pretrade_date": [pd.NaT, pd.Timestamp("2026-08-10")],
        }
    ).to_parquet(settings.paths.metadata / "trade_calendar.parquet", index=False)


def _plan(service: AuditedResumableDailySyncService) -> dict[str, object]:
    path = service.create_plan(as_of="2026-08-11")
    return json.loads(path.read_text(encoding="utf-8"))


def test_sync_checkpoint_records_hashes_and_rejects_artifact_corruption(tmp_path: Path):
    settings = _settings(tmp_path)
    _calendar(settings)
    service = AuditedResumableDailySyncService(settings)
    plan = _plan(service)
    plan_id = str(plan["plan_id"])
    state = service._load_apply_state(plan_id)
    state["run_attempt"] = 2

    marker = service._stage_root(plan_id) / "market" / "checkpoint.bin"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_bytes(b"verified")
    service._finish_step(state, "market_fetch", {"provider_calls": 3})

    record = state["steps"]["market_fetch"]
    assert record["checkpoint_contract_version"] == CHECKPOINT_CONTRACT_VERSION
    assert record["attempt"] == 2
    assert len(record["input_sha256"]) == 64
    assert len(record["output_sha256"]) == 64
    assert record["artifact_sha256"][str(marker)] == sha256_file(marker)
    assert service._verify_checkpoint_record(state, "market_fetch") is True

    marker.write_bytes(b"corrupt")
    with pytest.raises(SyncPlanInvalidatedError, match="artifact hash mismatch"):
        service._verify_checkpoint_record(state, "market_fetch")


def test_sync_checkpoint_rejects_semantic_output_tampering(tmp_path: Path):
    settings = _settings(tmp_path)
    _calendar(settings)
    service = AuditedResumableDailySyncService(settings)
    plan = _plan(service)
    state = service._load_apply_state(str(plan["plan_id"]))
    state["run_attempt"] = 1
    service._finish_step(state, "metadata_refresh", {"rows": 10})

    state["steps"]["metadata_refresh"]["output"]["rows"] = 11
    with pytest.raises(SyncPlanInvalidatedError, match="output hash mismatch"):
        service._verify_checkpoint_record(state, "metadata_refresh")


def test_legacy_sync_checkpoint_is_upgraded_before_reuse(tmp_path: Path):
    settings = _settings(tmp_path)
    _calendar(settings)
    service = AuditedResumableDailySyncService(settings)
    plan = _plan(service)
    plan_id = str(plan["plan_id"])
    state = service._load_apply_state(plan_id)
    state["run_attempt"] = 4
    state["steps"] = {
        "metadata_refresh": {
            "status": "SUCCEEDED",
            "finished_at_utc": "2026-09-16T00:00:00+00:00",
            "output": {"rows": 7},
        }
    }
    service._save_apply_state(state)

    assert service._verify_checkpoint_record(state, "metadata_refresh") is True
    upgraded = json.loads(service._apply_state_path(plan_id).read_text(encoding="utf-8"))
    record = upgraded["steps"]["metadata_refresh"]
    assert record["attempt"] == 4
    assert record["checkpoint_contract_version"] == CHECKPOINT_CONTRACT_VERSION
    assert len(record["input_sha256"]) == 64
    assert len(record["output_sha256"]) == 64


def test_sync_input_change_invalidates_checkpoint_and_downstream_reuse(tmp_path: Path):
    settings = _settings(tmp_path)
    _calendar(settings)
    service = AuditedResumableDailySyncService(settings)
    plan = _plan(service)
    plan_id = str(plan["plan_id"])
    state = service._load_apply_state(plan_id)
    service._finish_step(state, "market_fetch", {"provider_calls": 1})
    service._finish_step(state, "factor_reconcile", {"changed_symbol_count": 0})

    state["steps"]["market_fetch"]["status"] = "INVALIDATED"
    assert service._verify_checkpoint_record(state, "factor_reconcile") is False
    assert state["steps"]["factor_reconcile"]["status"] == "INVALIDATED"
    assert state["steps"]["factor_reconcile"]["invalidated_reason"] == "input_sha256_changed"


def test_sync_raw_promote_semantic_checkpoint_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    settings = _settings(tmp_path)
    _calendar(settings)
    service = AuditedResumableDailySyncService(settings)

    with pytest.raises(SyncPlanInvalidatedError, match="not a mapping"):
        service._verify_raw_promote_output({"output": []})
    service._verify_raw_promote_output({"output": {"raw_changes": "legacy"}})
    service._verify_raw_promote_output(
        {
            "output": {
                "raw_changes": [None, {}, {"dataset": "daily", "trade_date": "20260811"}]
            }
        }
    )

    record = {
        "output": {
            "raw_changes": [
                {
                    "dataset": "daily",
                    "trade_date": "20260811",
                    "new_content_sha256": "expected",
                }
            ]
        }
    }
    monkeypatch.setattr(service, "_partition_hash", lambda *_args: "actual")
    with pytest.raises(SyncPlanInvalidatedError, match="canonical Bronze"):
        service._verify_raw_promote_output(record)
    monkeypatch.setattr(service, "_partition_hash", lambda *_args: "expected")
    service._verify_raw_promote_output(record)


def test_sync_checkpoint_artifact_roots_are_hashed(tmp_path: Path):
    settings = _settings(tmp_path)
    _calendar(settings)
    service = AuditedResumableDailySyncService(settings)
    plan = _plan(service)
    plan_id = str(plan["plan_id"])
    stage_root = service._stage_root(plan_id)
    factor = stage_root / "factor_history" / "factor.parquet"
    market = stage_root / "market" / "daily" / "20260811.parquet"
    dividend = stage_root / "dividend" / "incoming.parquet"
    quality = service.plan_root / plan_id / "quality" / "raw_store.json"
    for path, content in (
        (factor, b"factor"),
        (market, b"market"),
        (dividend, b"dividend"),
        (quality, b"quality"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    factor_artifacts = service._checkpoint_artifacts(plan_id, "factor_reconcile")
    assert factor_artifacts[str(factor)] == sha256_file(factor)
    assert factor_artifacts[str(market)] == sha256_file(market)
    assert service._checkpoint_artifacts(plan_id, "dividend_fetch")[str(dividend)] == sha256_file(dividend)
    assert service._checkpoint_artifacts(plan_id, "raw_validate")[str(quality)] == sha256_file(quality)
    assert service._checkpoint_artifacts(plan_id, "metadata_refresh") == {}


def test_sync_qlib_publish_artifact_hashes_resolved_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    settings = _settings(tmp_path)
    _calendar(settings)
    service = AuditedResumableDailySyncService(settings)
    plan = _plan(service)
    manifest = tmp_path / "dataset_manifest.json"
    manifest.write_text('{"version_id":"v1"}', encoding="utf-8")
    monkeypatch.setattr(service, "_active_dataset_manifest", lambda: ({}, "v1"))
    monkeypatch.setattr(
        "qlib_platform.datasets.dataset_resolver.resolve_dataset",
        lambda *_args, **_kwargs: SimpleNamespace(manifest_path=manifest),
    )

    artifacts = service._checkpoint_artifacts(str(plan["plan_id"]), "qlib_publish")
    assert artifacts[str(manifest)] == sha256_file(manifest)


def test_sync_validate_existing_checkpoints_skips_incomplete_records(tmp_path: Path):
    settings = _settings(tmp_path)
    _calendar(settings)
    service = AuditedResumableDailySyncService(settings)
    plan = _plan(service)
    state = service._load_apply_state(str(plan["plan_id"]))
    state["steps"] = {
        "market_fetch": {"status": "RUNNING"},
        "factor_reconcile": "invalid-record",
    }
    service._certify_record(state, "market_fetch")
    service._validate_existing_checkpoints(state)
    assert state["steps"]["market_fetch"]["status"] == "RUNNING"


def test_sync_apply_attempt_is_durable_without_repeating_parent_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    settings = _settings(tmp_path)
    _calendar(settings)
    service = AuditedResumableDailySyncService(settings)
    plan = _plan(service)
    plan_id = str(plan["plan_id"])

    monkeypatch.setattr(
        ResumableCertifiedDailySyncService,
        "apply_plan",
        lambda self, current_plan_id, force_full=False: self._apply_state_path(current_plan_id),
    )

    service.apply_plan(plan_id)
    first = json.loads(service._apply_state_path(plan_id).read_text(encoding="utf-8"))
    service.apply_plan(plan_id)
    second = json.loads(service._apply_state_path(plan_id).read_text(encoding="utf-8"))

    assert first["run_attempt"] == 1
    assert second["run_attempt"] == 2
    assert second["checkpoint_contract_version"] == CHECKPOINT_CONTRACT_VERSION


def test_daily_run_state_keeps_v1_schema_and_tracks_attempts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings = _settings(tmp_path)
    runner = DailyResearchRun(settings)
    monkeypatch.setenv("GITHUB_SHA", "abc123")
    plan = {
        "plan_id": "plan-state",
        "target_session": "20260811",
        "config_sha256": "cfg",
        "watermarks": {},
        "endpoint_gaps": {},
    }

    first = runner._load_state(plan)
    second = runner._load_state(plan)

    assert first["schema_version"] == base.RUN_SCHEMA_VERSION
    assert second["schema_version"] == base.RUN_SCHEMA_VERSION
    assert second["daily_run_contract_version"] == DAILY_RUN_CONTRACT_VERSION
    assert first["run_attempt"] == 1
    assert second["run_attempt"] == 2


def test_daily_run_checkpoint_reuse_verifies_output_artifact(tmp_path: Path):
    settings = _settings(tmp_path)
    runner = DailyResearchRun(settings)
    artifact = tmp_path / "report.md"
    artifact.write_text("verified", encoding="utf-8")
    state: dict[str, object] = {"plan_id": "p", "run_attempt": 3, "steps": {}}

    runner._finish_step(
        state,
        "report",
        status="SUCCEEDED",
        input_hash="input",
        output={"path": str(artifact)},
    )
    record = state["steps"]["report"]  # type: ignore[index]
    assert record["attempt"] == 3
    assert record["artifact_sha256"][str(artifact)] == sha256_file(artifact)
    assert runner._step_reusable(state, "report", "input") is True

    artifact.write_text("corrupt", encoding="utf-8")
    with pytest.raises(RuntimeError, match="artifact hash mismatch"):
        runner._step_reusable(state, "report", "input")


def test_daily_run_dataset_manifest_hash_mismatch_fails_closed(tmp_path: Path):
    runner = DailyResearchRun(_settings(tmp_path))
    data_path = tmp_path / "dataset"
    data_path.mkdir()
    manifest = data_path / "dataset_manifest.json"
    manifest.write_text('{"version_id":"v1"}', encoding="utf-8")
    state: dict[str, object] = {"plan_id": "p", "run_attempt": 1, "steps": {}}
    runner._finish_step(
        state,
        "dataset_verify",
        status="SUCCEEDED",
        input_hash="dataset-input",
        output={
            "data_path": str(data_path),
            "dataset_manifest_sha256": "0" * 64,
        },
    )
    with pytest.raises(RuntimeError, match="manifest changed"):
        runner._step_reusable(state, "dataset_verify", "dataset-input")


def test_daily_run_feature_checkpoint_rejects_changed_materialization(tmp_path: Path):
    runner = DailyResearchRun(_settings(tmp_path))
    feature_root = tmp_path / "features"
    instrument_root = tmp_path / "instruments"
    feature_root.mkdir()
    instrument_root.mkdir()
    (feature_root / "a.bin").write_bytes(b"a")
    (instrument_root / "all.txt").write_text("A\n", encoding="utf-8")
    state: dict[str, object] = {"plan_id": "p", "run_attempt": 1, "steps": {}}
    runner._finish_step(
        state,
        "feature_materialization",
        status="SUCCEEDED",
        input_hash="features-input",
        output={
            "feature_root": str(feature_root),
            "instrument_root": str(instrument_root),
            "feature_file_count": 1,
            "instrument_file_count": 1,
        },
    )
    assert runner._step_reusable(state, "feature_materialization", "features-input") is True
    (feature_root / "unexpected.bin").write_bytes(b"extra")
    with pytest.raises(RuntimeError, match="feature materialization"):
        runner._step_reusable(state, "feature_materialization", "features-input")


def test_daily_run_legacy_checkpoint_is_not_blindly_reused(tmp_path: Path):
    runner = DailyResearchRun(_settings(tmp_path))
    state = {
        "steps": {
            "report": {
                "status": "SUCCEEDED",
                "input_sha256": "input",
                "output": {},
            }
        }
    }
    assert runner._step_reusable(state, "report", "input") is False


def _daily_state(apply_state: Path, *, dataset_version: str = "version-1") -> dict[str, object]:
    dataset_output = {
        "data_release_id": "ds-test",
        "dataset_version_id": dataset_version,
        "dataset_manifest_sha256": "a" * 64,
        "data_path": "/immutable/dataset",
    }
    regression_output = {"enabled": False}
    return {
        "plan_id": "transient-plan",
        "run_attempt": 2,
        "config_sha256": "cfg",
        "provider_watermarks": {"daily": {"last_success": "20260810"}},
        "endpoint_gaps": {"daily": ["20260811"]},
        "code": {"git_commit": "abc123", "package_version": "0.3.0"},
        "status": "SUCCEEDED",
        "steps": {
            "sync_publish": {
                "status": "SUCCEEDED",
                "attempt": 1,
                "input_sha256": "sync-input",
                "output_sha256": "sync-output",
                "artifact_sha256": {},
                "output": {"apply_state": str(apply_state)},
            },
            "dataset_verify": {
                "status": "SUCCEEDED",
                "attempt": 1,
                "input_sha256": "dataset-input",
                "output_sha256": "dataset-output",
                "artifact_sha256": {},
                "output": dataset_output,
            },
            "regression_backtest": {
                "status": "SKIPPED",
                "attempt": 1,
                "input_sha256": "regression-input",
                "output_sha256": base._identity(regression_output),
                "artifact_sha256": {},
                "output": regression_output,
            },
        },
    }


def test_business_identity_ignores_plan_attempt_but_tracks_frozen_inputs(tmp_path: Path):
    runner = DailyResearchRun(_settings(tmp_path))
    apply_state = tmp_path / "apply.json"
    apply_state.write_text('{"steps":{}}', encoding="utf-8")
    plan_a = {
        "plan_id": "random-a",
        "target_session": "20260811",
        "mode": "routine",
        "config_sha256": "cfg",
    }
    plan_b = dict(plan_a, plan_id="random-b")
    state_a = _daily_state(apply_state)
    state_b = _daily_state(apply_state)
    state_b["plan_id"] = "another-transient-plan"
    state_b["run_attempt"] = 9
    state_b_steps = state_b["steps"]
    assert isinstance(state_b_steps, dict)
    state_b_regression = state_b_steps["regression_backtest"]
    assert isinstance(state_b_regression, dict)
    state_b_regression["input_sha256"] = "regression-random-plan-b"

    first = runner._business_run_id(plan_a, state_a)
    second = runner._business_run_id(plan_b, state_b)
    assert first == second

    changed_dataset = _daily_state(apply_state, dataset_version="version-2")
    assert runner._business_run_id(plan_b, changed_dataset) != first
    changed_config = dict(state_b, config_sha256="cfg-v2")
    assert runner._business_run_id(plan_b, changed_config) != first


def test_lineage_checkpoint_ledger_report_and_manifest_are_auditable(tmp_path: Path):
    settings = _settings(tmp_path)
    runner = DailyResearchRun(settings)
    freshness_output = {
        "passed": True,
        "benchmark": {"symbol": "SH000300", "fresh": True},
        "universe": {"kind": "all", "fresh": True},
    }
    apply_state = tmp_path / "apply.json"
    apply_state.write_text(
        json.dumps(
            {
                "status": "SUCCEEDED",
                "steps": {
                    "freshness_gate": {
                        "status": "SUCCEEDED",
                        "attempt": 1,
                        "input_sha256": "fresh-input",
                        "output_sha256": base._identity(freshness_output),
                        "artifact_sha256": {},
                        "output": freshness_output,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    state = _daily_state(apply_state)
    plan = {
        "plan_id": "plan-final",
        "target_session": "20260811",
        "status": "PLANNED",
        "mode": "routine",
        "config_sha256": "cfg",
    }

    lineage = runner._lineage(plan, state)
    ledger = runner._checkpoint_ledger(plan, state)
    assert lineage["immutable_dataset"]["data_release_id"] == "ds-test"
    assert lineage["benchmark"]["symbol"] == "SH000300"
    assert lineage["model_policy"]["automatic_selection"] is False
    assert ledger["calendar_preflight"]["status"] == "SUCCEEDED"
    assert ledger["sync.freshness_gate"]["output_sha256"] == base._identity(freshness_output)

    report = runner._render_report(plan, state)  # type: ignore[arg-type]
    report_text = report.read_text(encoding="utf-8")
    assert "## Audit lineage" in report_text
    assert "Business run identity" in report_text
    assert "Automatic model selection/promotion: `false/false`" in report_text

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(state), encoding="utf-8")
    enriched = json.loads(runner._enrich_manifest(manifest, plan).read_text(encoding="utf-8"))
    assert enriched["daily_run_contract_version"] == DAILY_RUN_CONTRACT_VERSION
    assert enriched["checkpoint_contract_version"] == CHECKPOINT_CONTRACT_VERSION
    assert enriched["business_run_id"].startswith("dailybiz-")
    assert "sync.freshness_gate" in enriched["checkpoint_ledger"]
