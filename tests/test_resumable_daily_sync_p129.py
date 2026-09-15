from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import qlib_platform.data.resumable_certified_sync as resumable_module
from qlib_platform.data.planned_daily_sync import SyncPlanInvalidatedError
from qlib_platform.data.resumable_certified_sync import ResumableCertifiedDailySyncService
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


def _write_apply_state(service: ResumableCertifiedDailySyncService, plan_id: str, status: str) -> None:
    path = service._apply_state_path(plan_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "plan_id": plan_id,
                "status": status,
                "steps": {},
                "context": {
                    "changed_trade_dates": [],
                    "revised_symbols": [],
                    "pit_changed": False,
                },
            }
        ),
        encoding="utf-8",
    )


def test_hard_crash_reuses_running_plan_without_new_provider_plan(tmp_path: Path):
    settings = _settings(tmp_path)
    _calendar(settings)
    service = ResumableCertifiedDailySyncService(settings)

    first = service.create_plan(as_of="2026-08-11")
    first_payload = json.loads(first.read_text(encoding="utf-8"))
    _write_apply_state(service, first_payload["plan_id"], "RUNNING")

    resumed = service.create_plan(as_of="2026-08-11")

    assert resumed == first
    assert json.loads(resumed.read_text(encoding="utf-8"))["plan_id"] == first_payload["plan_id"]


def test_failed_semantic_run_does_not_auto_reuse_plan(tmp_path: Path):
    settings = _settings(tmp_path)
    _calendar(settings)
    service = ResumableCertifiedDailySyncService(settings)

    first = service.create_plan(as_of="2026-08-11")
    first_payload = json.loads(first.read_text(encoding="utf-8"))
    _write_apply_state(service, first_payload["plan_id"], "FAILED")

    replacement = service.create_plan(as_of="2026-08-11")
    replacement_payload = json.loads(replacement.read_text(encoding="utf-8"))

    assert replacement_payload["plan_id"] != first_payload["plan_id"]


def _published_manifest(path: Path, *, run_id: str, coverage_end: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "3.0",
                "version_id": "version-current",
                "coverage": {"start": "2026-08-10", "end": coverage_end},
                "mode": "update",
                "data_release_id": "ds_current",
                "sync_context": {"run_id": run_id},
            }
        ),
        encoding="utf-8",
    )


def test_stale_plan_cannot_roll_active_dataset_alias_back(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    service = ResumableCertifiedDailySyncService(settings)
    manifest = tmp_path / "published" / "dataset_manifest.json"
    _published_manifest(manifest, run_id="newer-plan", coverage_end="2026-08-11")
    monkeypatch.setattr(
        resumable_module,
        "resolve_dataset",
        lambda *args, **kwargs: SimpleNamespace(manifest_path=manifest, version_id="version-current"),
    )

    with pytest.raises(SyncPlanInvalidatedError, match="active DatasetVersion"):
        service._guard_monotonic_active_alias(
            {"plan_id": "older-plan", "target_session": "20260810"}
        )


def test_publish_receipt_recovers_crash_after_alias_promotion(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    service = ResumableCertifiedDailySyncService(settings)
    plan_id = "syncplan-20260811-receipt"
    manifest = tmp_path / "published" / "dataset_manifest.json"
    _published_manifest(manifest, run_id=plan_id, coverage_end="2026-08-11")
    monkeypatch.setattr(
        resumable_module,
        "resolve_dataset",
        lambda *args, **kwargs: SimpleNamespace(manifest_path=manifest, version_id="version-current"),
    )
    state = {
        "schema_version": "1.0",
        "plan_id": plan_id,
        "status": "RUNNING",
        "steps": {
            name: {"status": "SUCCEEDED", "output": {}}
            for name in ("extended_sync", "pit_refresh", "metadata_refresh", "freshness_gate")
        },
        "context": {
            "changed_trade_dates": ["20260811"],
            "revised_symbols": [],
            "pit_changed": False,
        },
    }

    recovered = service._recover_publish_receipt(
        {"plan_id": plan_id, "target_session": "20260811"}, state
    )

    assert recovered is True
    assert state["steps"]["qlib_publish"]["status"] == "SUCCEEDED"
    result = state["steps"]["qlib_publish"]["output"]["result"]
    assert result["dataset_version_id"] == "version-current"
    assert result["data_release_id"] == "ds_current"
    assert result["recovered_publish_receipt"] is True
    pending = json.loads(service._pending_publish_path().read_text(encoding="utf-8"))
    assert pending["status"] == "clear"
