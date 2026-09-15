from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import qlib_platform.runtime.scheduler as scheduler


def _settings():
    return SimpleNamespace(
        data={
            "production": {
                "daily_run": {
                    "schedule": {
                        "time": "18:30",
                        "timezone": "Asia/Shanghai",
                    }
                }
            }
        }
    )


def test_scheduler_main_accepts_absolute_config(tmp_path: Path, monkeypatch):
    python_exe = tmp_path / "python"
    python_exe.write_text("", encoding="utf-8")
    config = tmp_path / "pipeline.yaml"
    config.write_text("stub: true\n", encoding="utf-8")
    output = tmp_path / "rendered"

    monkeypatch.setattr(scheduler.Settings, "load", lambda *args, **kwargs: _settings())
    monkeypatch.setattr(
        "sys.argv",
        [
            "tq-render-scheduler",
            "--kind",
            "systemd",
            "--repo-root",
            str(tmp_path),
            "--python-exe",
            str(python_exe),
            "--config",
            str(config.resolve()),
            "--output-dir",
            str(output),
        ],
    )

    scheduler.main()

    assert (output / "qlib-platform-daily-sync.service").is_file()


def test_scheduler_main_rejects_missing_config(tmp_path: Path, monkeypatch):
    python_exe = tmp_path / "python"
    python_exe.write_text("", encoding="utf-8")
    missing = tmp_path / "missing-pipeline.yaml"

    monkeypatch.setattr(
        "sys.argv",
        [
            "tq-render-scheduler",
            "--kind",
            "systemd",
            "--repo-root",
            str(tmp_path),
            "--python-exe",
            str(python_exe),
            "--config",
            str(missing),
            "--output-dir",
            str(tmp_path / "rendered"),
        ],
    )

    with pytest.raises(FileNotFoundError):
        scheduler.main()


def test_scheduler_render_rejects_unresolved_template_marker(tmp_path: Path, monkeypatch):
    deploy = tmp_path / "deploy"
    systemd = deploy / "systemd"
    systemd.mkdir(parents=True)
    (systemd / "qlib-platform-daily-sync.service.in").write_text(
        "WorkingDirectory=@REPO_ROOT@\nExecStart=@PYTHON_EXE@ --config @CONFIG_PATH@\nUnexpected=@UNKNOWN@\n",
        encoding="utf-8",
    )
    (systemd / "qlib-platform-daily-sync.timer").write_text(
        "OnCalendar=@SCHEDULE_TIME@:00 @SCHEDULE_TIMEZONE@\n",
        encoding="utf-8",
    )
    python_exe = tmp_path / "python"
    python_exe.write_text("", encoding="utf-8")
    config = tmp_path / "pipeline.yaml"
    config.write_text("stub: true\n", encoding="utf-8")

    monkeypatch.setattr(scheduler, "resource_path", lambda _relative: deploy)

    with pytest.raises(ValueError, match="unresolved scheduler template marker"):
        scheduler.render(
            "systemd",
            tmp_path,
            python_exe,
            config,
            tmp_path / "rendered",
        )


@pytest.mark.parametrize(
    "module",
    [
        "qlib_platform.runtime.daily_research_run",
        "qlib_platform.runtime.production_daily_run",
    ],
)
def test_daily_run_module_entrypoints_expose_help(module: str):
    completed = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "--as-of" in completed.stdout
