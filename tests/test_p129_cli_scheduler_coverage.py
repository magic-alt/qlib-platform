from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import qlib_platform.runtime.daily_research_run as daily_run
import qlib_platform.runtime.production_daily_run as production_run
import qlib_platform.runtime.scheduler as scheduler


def test_scheduler_helpers_validate_paths_and_schedule(tmp_path: Path):
    directory = tmp_path / "work"
    directory.mkdir()
    executable = tmp_path / "python"
    executable.write_text("", encoding="utf-8")

    assert scheduler._absolute_existing(str(directory)) == directory.resolve()
    assert scheduler._absolute_existing(str(executable), file=True) == executable.resolve()
    with pytest.raises(FileNotFoundError):
        scheduler._absolute_existing(str(tmp_path / "missing"))
    with pytest.raises(FileNotFoundError):
        scheduler._absolute_existing(str(directory), file=True)

    settings = SimpleNamespace(
        data={
            "production": {
                "daily_run": {
                    "schedule": {"time": "19:05", "timezone": "Asia/Shanghai"}
                }
            }
        }
    )
    assert scheduler._schedule(settings) == ("19:05", "Asia/Shanghai")

    fallback = SimpleNamespace(data={"data_sync": {"timezone": "Asia/Singapore"}})
    assert scheduler._schedule(fallback) == ("18:30", "Asia/Singapore")

    invalid_time = SimpleNamespace(
        data={"production": {"daily_run": {"schedule": {"time": "25:00"}}}}
    )
    with pytest.raises(ValueError):
        scheduler._schedule(invalid_time)

    invalid_zone = SimpleNamespace(
        data={
            "production": {
                "daily_run": {"schedule": {"time": "18:30", "timezone": "   "}}
            }
        }
    )
    with pytest.raises(ValueError, match="timezone"):
        scheduler._schedule(invalid_zone)


def test_scheduler_render_rejects_unknown_kind(tmp_path: Path):
    executable = tmp_path / "python"
    executable.write_text("", encoding="utf-8")
    config = tmp_path / "pipeline.yaml"
    config.write_text("project_root: ./data\nqlib: {}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported scheduler kind"):
        scheduler.render(
            "unknown",
            tmp_path,
            executable,
            config,
            tmp_path / "out",
        )


def test_scheduler_main_uses_local_relative_config(tmp_path: Path, monkeypatch, capsys):
    executable = tmp_path / "python"
    executable.write_text("", encoding="utf-8")
    config = tmp_path / "pipeline.yaml"
    config.write_text("stub: true\n", encoding="utf-8")
    output = tmp_path / "rendered"
    fake_settings = SimpleNamespace(
        data={
            "production": {
                "daily_run": {
                    "schedule": {"time": "19:05", "timezone": "Asia/Shanghai"}
                }
            }
        }
    )
    monkeypatch.setattr(scheduler.Settings, "load", lambda *args, **kwargs: fake_settings)
    monkeypatch.setattr(
        "sys.argv",
        [
            "tq-render-scheduler",
            "--kind",
            "systemd",
            "--repo-root",
            str(tmp_path),
            "--python-exe",
            str(executable),
            "--config",
            "pipeline.yaml",
            "--output-dir",
            str(output),
        ],
    )

    scheduler.main()
    rendered = capsys.readouterr().out
    assert "qlib-platform-daily-sync.service" in rendered
    assert "19:05:00 Asia/Shanghai" in (output / "qlib-platform-daily-sync.timer").read_text(
        encoding="utf-8"
    )


def test_code_provenance_prefers_environment_and_falls_back_to_git(tmp_path: Path, monkeypatch):
    settings = SimpleNamespace(config_path=tmp_path / "configs" / "pipeline.yaml")
    monkeypatch.setenv("QLIB_PLATFORM_GIT_SHA", "env-sha")
    assert production_run._code_provenance(settings)["git_commit"] == "env-sha"

    monkeypatch.delenv("QLIB_PLATFORM_GIT_SHA")
    monkeypatch.delenv("GITHUB_SHA", raising=False)

    def package_missing(_name: str):
        raise production_run.PackageNotFoundError

    monkeypatch.setattr(production_run, "version", package_missing)
    monkeypatch.setattr(
        production_run.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="git-sha\n"),
    )
    provenance = production_run._code_provenance(settings)
    assert provenance == {"git_commit": "git-sha", "package_version": "source-checkout"}

    def git_unavailable(*args, **kwargs):
        raise OSError("git missing")

    monkeypatch.setattr(production_run.subprocess, "run", git_unavailable)
    assert production_run._code_provenance(settings)["git_commit"] is None


def test_production_backfill_blocks_published_sessions_and_delegates_new_sessions(
    tmp_path: Path, monkeypatch
):
    runner = object.__new__(production_run.DailyResearchRun)
    sync = SimpleNamespace(
        _local_open_dates=lambda start, end: ["20260810", "20260811"],
        _active_dataset_manifest=lambda: ({"coverage": {"end": "2026-08-10"}}, "version-1"),
        _manifest_end=production_run.ResumableCertifiedDailySyncService._manifest_end,
    )
    runner.sync = sync

    with pytest.raises(ValueError, match="backfill may only advance"):
        runner.backfill("2026-08-10", "2026-08-11", mode="routine", force_full=False)

    sync._local_open_dates = lambda start, end: ["20260811"]
    sync._active_dataset_manifest = lambda: None
    expected = [tmp_path / "result.json"]
    monkeypatch.setattr(
        daily_run.DailyResearchRun,
        "backfill",
        lambda self, start, end, *, mode, force_full: expected,
    )
    assert runner.backfill("2026-08-11", "2026-08-11", mode="routine", force_full=False) == expected


def test_production_dataset_materialization_fails_closed(tmp_path: Path, monkeypatch):
    runner = object.__new__(production_run.DailyResearchRun)
    runner.root = tmp_path / "state"
    runner.settings = SimpleNamespace()
    plan = {"plan_id": "plan", "target_session": "20260811"}
    state = {"plan_id": "plan", "steps": {}}

    def base_dataset(root: Path) -> dict[str, str]:
        return {
            "dataset_version_id": "version-1",
            "dataset_manifest_sha256": "sha",
            "data_path": str(root),
        }

    monkeypatch.setattr(
        daily_run.DailyResearchRun,
        "_verify_dataset",
        lambda self, plan, state: base_dataset(tmp_path / "missing-features"),
    )
    with pytest.raises(RuntimeError, match="no materialized Qlib features"):
        runner._verify_dataset(plan, state)

    dataset = tmp_path / "missing-instruments"
    feature = dataset / "features" / "sh600000"
    feature.mkdir(parents=True)
    (feature / "close.day.bin").write_bytes(b"x")
    monkeypatch.setattr(
        daily_run.DailyResearchRun,
        "_verify_dataset",
        lambda self, plan, state: base_dataset(dataset),
    )
    with pytest.raises(RuntimeError, match="no materialized Qlib instruments"):
        runner._verify_dataset(plan, state)


def test_base_daily_run_and_backfill_helpers(tmp_path: Path):
    runner = object.__new__(daily_run.DailyResearchRun)
    plan_files: dict[str, Path] = {}

    def plan(*, as_of: str | None, mode: str) -> Path:
        key = str(as_of or "latest")
        path = tmp_path / f"{key}.json"
        path.write_text(json.dumps({"plan_id": f"plan-{key}", "mode": mode}), encoding="utf-8")
        plan_files[key] = path
        return path

    executed: list[tuple[str, bool, bool | None, bool]] = []

    def execute_plan(
        plan_id: str,
        *,
        force_full: bool = False,
        regression: bool | None = None,
        backfill: bool = False,
    ) -> Path:
        executed.append((plan_id, force_full, regression, backfill))
        result = tmp_path / f"{plan_id}.result.json"
        result.write_text('{"status":"SUCCEEDED"}', encoding="utf-8")
        return result

    runner.plan = plan  # type: ignore[method-assign]
    runner.execute_plan = execute_plan  # type: ignore[method-assign]
    runner.sync = SimpleNamespace(_local_open_dates=lambda start, end: ["20260810", "20260811"])

    result = runner.run(
        as_of="20260811",
        mode="routine",
        force_full=True,
        regression=True,
    )
    assert result.name == "plan-20260811.result.json"
    assert executed[-1] == ("plan-20260811", True, True, False)

    outputs = runner.backfill(
        "20260810",
        "20260811",
        mode="routine",
        force_full=False,
    )
    assert len(outputs) == 2
    assert executed[-2:] == [
        ("plan-20260810", False, False, True),
        ("plan-20260811", False, False, True),
    ]


def test_base_parser_and_main_branches(tmp_path: Path, monkeypatch, capsys):
    parsed = daily_run.parser().parse_args(
        ["--as-of", "2026-08-11", "--mode", "historical-audit", "--regression"]
    )
    assert parsed.as_of == "2026-08-11"
    assert parsed.mode == "historical-audit"
    assert parsed.regression is True

    plan_path = tmp_path / "plan.json"
    plan_path.write_text('{"plan_id":"plan-1"}', encoding="utf-8")
    result_path = tmp_path / "result.json"
    result_path.write_text('{"status":"SUCCEEDED"}', encoding="utf-8")

    class _Runner:
        def __init__(self, settings) -> None:
            self.settings = settings

        def plan(self, **kwargs) -> Path:
            del kwargs
            return plan_path

        def execute_plan(self, *args, **kwargs) -> Path:
            del args, kwargs
            return result_path

        def backfill(self, *args, **kwargs) -> list[Path]:
            del args, kwargs
            return [result_path]

    monkeypatch.setattr(daily_run, "DailyResearchRun", _Runner)
    monkeypatch.setattr(
        daily_run,
        "Settings",
        SimpleNamespace(load=lambda *args, **kwargs: SimpleNamespace()),
    )

    def run_with(namespace: SimpleNamespace) -> str:
        monkeypatch.setattr(
            daily_run,
            "parser",
            lambda: SimpleNamespace(parse_args=lambda: namespace),
        )
        assert daily_run.main() == 0
        return capsys.readouterr().out

    common = {
        "config": str(tmp_path / "pipeline.yaml"),
        "mode": "routine",
        "force_full": False,
        "regression": None,
    }
    assert "plan-1" in run_with(
        SimpleNamespace(**common, resume=None, plan=True, backfill=None, as_of="2026-08-11")
    )
    assert "SUCCEEDED" in run_with(
        SimpleNamespace(**common, resume="plan-1", plan=False, backfill=None, as_of=None)
    )
    assert "backfill" in run_with(
        SimpleNamespace(
            **common,
            resume=None,
            plan=False,
            backfill=("2026-08-10", "2026-08-11"),
            as_of=None,
        )
    )
    assert "SUCCEEDED" in run_with(
        SimpleNamespace(**common, resume=None, plan=False, backfill=None, as_of="2026-08-11")
    )

    with pytest.raises(ValueError, match="resume cannot be combined"):
        run_with(
            SimpleNamespace(
                **common,
                resume="plan-1",
                plan=True,
                backfill=None,
                as_of=None,
            )
        )
    with pytest.raises(ValueError, match="backfill cannot be combined"):
        run_with(
            SimpleNamespace(
                **common,
                resume=None,
                plan=False,
                backfill=("2026-08-10", "2026-08-11"),
                as_of="2026-08-11",
            )
        )


def test_production_main_rejects_invalid_argument_combinations(monkeypatch):
    common = {
        "config": "pipeline.yaml",
        "mode": "routine",
        "force_full": False,
        "regression": None,
    }

    def parse(namespace: SimpleNamespace) -> None:
        monkeypatch.setattr(
            production_run.base,
            "parser",
            lambda: SimpleNamespace(parse_args=lambda: namespace),
        )

    parse(
        SimpleNamespace(
            **common,
            resume="plan-1",
            plan=True,
            backfill=None,
            as_of=None,
        )
    )
    with pytest.raises(ValueError, match="resume cannot be combined"):
        production_run.main()

    parse(
        SimpleNamespace(
            **common,
            resume=None,
            plan=False,
            backfill=("2026-08-10", "2026-08-11"),
            as_of="2026-08-11",
        )
    )
    with pytest.raises(ValueError, match="backfill cannot be combined"):
        production_run.main()
