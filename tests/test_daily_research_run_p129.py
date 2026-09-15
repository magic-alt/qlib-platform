from __future__ import annotations

import json
from pathlib import Path

import pytest

from qlib_platform.runtime.production_daily_run import DailyResearchRun
from qlib_platform.settings import Paths, Settings


def _settings(tmp_path: Path) -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    return Settings(
        config_path=tmp_path / "configs" / "pipeline.yaml",
        data={
            "start_date": "20200101",
            "end_date": "20200110",
            "data_source": {"kind": "tushare"},
            "qlib": {
                "dataset_dir": "unused",
                "dataset_name": "test",
                "dataset_version": "test",
                "dataset_ref": "test-current",
                "include_fields": [],
            },
            "data_sync": {"timezone": "Asia/Shanghai", "ready_after": "17:30"},
            "universe": {"instruments": "all"},
        },
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=paths.root / "qlib" / "test",
    )


class _FakeSync:
    def __init__(self, root: Path, plan: dict[str, object], *, fail_apply: bool = False) -> None:
        self.root = root
        self.plan = plan
        self.fail_apply = fail_apply
        self.apply_calls = 0
        self.plan_path = root / "plan.json"
        self.plan_path.parent.mkdir(parents=True, exist_ok=True)
        self.plan_path.write_text(json.dumps(plan), encoding="utf-8")

    def load_plan(self, plan_id: str) -> dict[str, object]:
        assert plan_id == self.plan["plan_id"]
        return dict(self.plan)

    def apply_plan(self, plan_id: str, *, force_full: bool = False) -> Path:
        del force_full
        assert plan_id == self.plan["plan_id"]
        self.apply_calls += 1
        if self.fail_apply:
            raise RuntimeError("provider required endpoint unavailable")
        path = self.root / "apply.json"
        path.write_text('{"status":"SUCCEEDED"}', encoding="utf-8")
        return path

    def _plan_path(self, plan_id: str) -> Path:
        assert plan_id == self.plan["plan_id"]
        return self.plan_path


def test_non_trading_session_is_explicit_skip_without_apply(tmp_path: Path):
    settings = _settings(tmp_path)
    runner = DailyResearchRun(settings)
    plan = {
        "plan_id": "syncplan-20200104-skip",
        "target_session": "20200104",
        "status": "SKIPPED_NON_TRADING_DAY",
        "config_sha256": "cfg",
    }
    fake = _FakeSync(tmp_path / "fake", plan)
    runner.sync = fake  # type: ignore[assignment]

    manifest_path = runner.execute_plan(str(plan["plan_id"]))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert fake.apply_calls == 0
    assert manifest["status"] == "SKIPPED_NON_TRADING_DAY"


def test_sync_failure_blocks_dataset_and_regression_nodes(tmp_path: Path):
    settings = _settings(tmp_path)
    runner = DailyResearchRun(settings)
    plan = {
        "plan_id": "syncplan-20200102-fail",
        "target_session": "20200102",
        "status": "PLANNED",
        "config_sha256": "cfg",
    }
    fake = _FakeSync(tmp_path / "fake", plan, fail_apply=True)
    runner.sync = fake  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="required endpoint unavailable"):
        runner.execute_plan(str(plan["plan_id"]))

    state_path = settings.paths.state / "daily_run" / "runs" / str(plan["plan_id"]) / "run_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "FAILED"
    assert state["steps"]["sync_publish"]["status"] == "FAILED"
    assert state["steps"]["dataset_verify"]["status"] == "BLOCKED"
    assert state["steps"]["regression_backtest"]["status"] == "BLOCKED"
    assert state["steps"]["notification"]["status"] == "BLOCKED"
